# Etapa 01.1 — Fundação multi-tenant

Branch: `feat/etapa-01-01-multitenant-core` (a partir de `chore/etapa-00-stabilization` @ `bf488a3`).
Esta etapa entrega a **fundação** e os **primeiros recursos protegidos** (Brand, Product, CustomerProfile).
**O isolamento NÃO é completo**: veja a seção 7 (o que ainda não está isolado).

## 1. Modelo

| Tabela | Função |
|---|---|
| `organizations` | Tenant (`id`, `name`, `slug` único, `is_active`). |
| `organization_members` | Vínculo usuário↔organização: `role` (owner/admin/manager/analyst/viewer), `status` (active/revoked), `revoked_at`. `UNIQUE(organization_id,user_id)`, CHECKs de role/status. Vínculo é **revogado, nunca apagado**. |
| `brands`, `products`, `customer_profiles`, `brand_profiles` | Ganham `organization_id` (FK `RESTRICT`, indexada). |

Integridade no banco: `UNIQUE(id, organization_id)` em `brands` e `customer_profiles`; FKs **compostas**
`products(brand_id, organization_id)→brands`, `brand_profiles(brand_id, organization_id)→brands` e
`brand_profiles(profile_id, organization_id)→customer_profiles`. Um produto de uma organização não pode apontar
para a marca de outra, nem um vínculo marca↔perfil cruzar organizações, **mesmo com bug na aplicação**.
Um trigger copia a organização da marca para novas linhas de `brand_profiles` (o ORM insere essa tabela implicitamente).

## 2. RBAC por organização (`app/core/tenancy.py`)

| Permissão | viewer | analyst | manager | admin | owner |
|---|:-:|:-:|:-:|:-:|:-:|
| `*:read` (brands/products/profiles), `members:read`, `org:read` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `reports:read` | | ✔ | ✔ | ✔ | ✔ |
| `*:write` / `*:delete` (brands/products/profiles) | | | ✔ | ✔ | ✔ |
| `members:manage` | | | | ✔ | ✔ |
| `org:manage` | | | | | ✔ |

Regras de membros: só **owner** concede/altera/revoga o papel owner; a organização nunca fica sem owner;
qualquer membro pode **sair** sozinho; re-adicionar um membro revogado é uma nova concessão explícita.

**Papéis globais não dão acesso a dados de tenant.** `User.roles`/`is_superuser` continuam valendo só para
funções de plataforma (ex.: criar usuários). Os endpoints migrados não consultam `has_permission`/`has_role`.

## 3. Contexto de organização

`get_current_org` (dependência central): usuário autenticado e ativo → organização pelo header
`X-Organization-Id` (ou, sem header, a única membership ativa; com várias → **400**) → membership **ativa** em
organização **ativa** → senão **403**. Organização inexistente, de outro tenant, revogada ou inativa respondem
**o mesmo 403** (sem oráculo de existência). `require_org_permission("brands:write")` soma a checagem RBAC.
Recursos de outro tenant respondem **404**, indistinguível de inexistente.

## 4. Row Level Security

* Papel de runtime `ads_app` (`NOLOGIN NOSUPERUSER NOBYPASSRLS`, criado pela migration `e3f1a7c2d9b4`). A aplicação faz
  `SET ROLE ads_app` em **toda** conexão do pool (`app/database.py`) — vale mesmo que o login seja superuser (o `postgres`
  padrão). `DB_RUNTIME_ROLE=` vazio desliga isso para quem conecta com um login já restrito.
* `ENABLE` + `FORCE ROW LEVEL SECURITY` e a política `tenant_isolation` (`USING` e `WITH CHECK`):
  `organization_id = NULLIF(current_setting('app.current_org', true), '')`. Sem contexto → **nenhuma linha** (fail closed).
* Contexto **transacional**: `set_config('app.current_org', :org, true)` é reescrito no `BEGIN` de **toda** transação de toda sessão
  (vazio quando não há organização) e morre no COMMIT/ROLLBACK → uma conexão reutilizada nunca carrega o tenant anterior.
* Defesa em profundidade: toda query dos routers também filtra `organization_id`; um hook `before_flush` carimba a organização da
  sessão e **recusa** objeto de outra organização.
* Guarda de startup (`verify_runtime_role_is_safe`): a API **não sobe** se o papel for superuser/BYPASSRLS ou se as tabelas de
  tenant não estiverem com RLS forçado.
* Concessões: DML no schema + `DEFAULT PRIVILEGES` (tabelas de migrations futuras) para `ads_app`; sem acesso a `alembic_version`.

## 5. Migrations (cadeia incremental a partir de `a1d2e3f4b5c6`)

| Revisão | Conteúdo |
|---|---|
| `d4a9c1e7b2f0` | `organizations`, `organization_members`; **organização pessoal (owner)** para cada usuário existente. Nenhum dado de negócio é atribuído. |
| `e2b3c4d5e6f7` | `organization_id` + índices + FKs/uniques compostos + `CHECK (organization_id IS NOT NULL) NOT VALID` + trigger. |
| `e3f1a7c2d9b4` | Papel `ads_app`, grants, RLS forçado e política `tenant_isolation`. |

**Baseline congelado.** `fba8f217905e` chamava `Base.metadata.create_all` com os modelos *atuais*; qualquer mudança de modelo
entrava no baseline e colidia com migrations incrementais. Agora é um **DDL estático** (gerado dos modelos de `bf488a3`,
todo `IF NOT EXISTS`, sem importar `app.models`). `alembic check`: sem divergência modelos × migrations.
`init_db.py` passou a rodar `alembic upgrade head` (nunca `create_all`, que não cria RLS/trigger/papel).

## 6. Dados legados — estratégia

Registros anteriores ao multi-tenant **não têm dono conhecido**. A migration **não adivinha**: ficam com `organization_id = NULL`,
**invisíveis para todas as organizações** (a política não casa NULL), e o `CHECK … NOT VALID` impede **novos** registros sem dono
sem exigir que os antigos já estejam corrigidos. Quem *sabe* de quem é cada dado usa a ferramenta de operador
(papel privilegiado, pois as linhas ocultas não são visíveis ao `ads_app`):

```bash
cd backend
ADMIN_DATABASE_URL=postgresql://… python -m app.scripts.claim_legacy_data report
python -m app.scripts.claim_legacy_data claim --org-id <ORG> --brands all --profiles all            # dry run
python -m app.scripts.claim_legacy_data claim --org-id <ORG> --brands id1,id2 --profiles id3 --apply
```

Marcas levam seus produtos e vínculos; a ferramenta **recusa** (sem alterar nada) separar uma marca do perfil a que está ligada,
reivindicar linhas já atribuídas, ou rodar com papel sujeito a RLS. **Efeito colateral esperado:** num ambiente com dados
reais, depois de migrar, as telas de Marcas/Produtos/Perfis ficam vazias até o claim.

## 7. O que AINDA NÃO está isolado (sem multi-tenant)

**Tabelas sem `organization_id`/RLS** (qualquer usuário autenticado alcança via os endpoints correspondentes):
`generated_ads`, `winning_ads` (templates), `scraped_ads`, `saved_searches`, `search_logs`, `api_usage_logs`, `verticals`,
`facebook_pages`, `page_blacklist`, `keyword_blacklist`, `brand_scrapes`, `brand_scraped_ads`, `prompts`, `ad_styles`,
`facebook_campaigns`, `facebook_adsets`, `facebook_ads`, `api_keys`, `mutation_audits`.

**Conexões de anúncios** (`google_ads_connections`, `meta_ads_connections`, `tiktok_ads_connections`) são por **usuário**, não por
organização; o Facebook (`/facebook/*`) ainda usa o token global do ambiente; `/optimization/*` e `/bot/*` continuam por usuário/chave.

**Endpoints sem contexto de organização**: `generated-ads`, `templates`, `research`, `facebook`, `google-ads`, `tiktok-ads`,
`overview`, `prompts`, `ad-styles`, `copy-generation`, `uploads` (arquivos em `/uploads` públicos por URL), `bot`, `optimization`,
`dashboard` (apenas os contadores de marcas/produtos são por organização; o resto do corpo não é).

**Referências cruzadas**: `generated_ads.brand_id`/`product_id` são strings sem FK → um usuário pode gravar um anúncio com o id
de uma marca de outro tenant (a marca em si continua inacessível).

## 8. Riscos restantes e decisões

* `ads_app` pode executar `set_config('app.current_org', …)`: RLS protege contra bugs de aplicação, **não** contra injeção de SQL
  arbitrário na sessão. Mitigação futura: contexto assinado/`SECURITY DEFINER`.
* `organizations`/`organization_members` **não** têm RLS (são a tabela de controle; toda query é filtrada pelas memberships do usuário).
* O frontend **não** envia `X-Organization-Id` (restrição desta etapa): usuário com **várias** organizações recebe 400 até existir
  um seletor de organização. Hoje todo usuário tem exatamente uma (a pessoal).
* IDs gerados pelo cliente (`crypto.randomUUID()`) são aceitos só se forem UUID válidos; colisão com linha de outro tenant → 409
  (oráculo desprezível para UUID v4).
* Adicionar membro por e-mail revela se o e-mail existe na plataforma (404 vs 201) a quem tem `members:manage`.
* `DeconstructRequest.template_id` ainda é `int` (preexistente, fora do escopo); corrigi `ReconstructRequest` (ids `str`) porque
  o endpoint rejeitava toda requisição real com 422 e passou a depender do contexto de organização.
* Sem auditoria de negações de acesso/alterações de membros (próxima etapa).

## 9. Próximas migrations sugeridas

1. Validar e endurecer: após o claim, `ALTER TABLE … VALIDATE CONSTRAINT ck_*_org_required` + `SET NOT NULL`, e remover o CHECK.
2. `organization_id` + RLS em `generated_ads`, `winning_ads`, `prompts`, `ad_styles`, `scraped_ads`/pesquisas e `brand_scrapes*`.
3. Conexões de anúncios (Google/Meta/TikTok) por organização + credenciais por organização; `api_keys`/`mutation_audits` com `organization_id`.
4. RLS em `organization_members` (política por `user_id` do contexto) e auditoria de membros.
5. Seletor de organização no frontend + header `X-Organization-Id`.

## 10. Testes

* `tests/unit/test_multitenancy_api.py` — A/B, membership ativa/revogada/inexistente, org inativa, papéis globais sem bypass,
  CRUD cruzado, IDOR (brand/product/profile), links e reatribuição entre tenants, colisão de id, RBAC por papel, API de membros.
* `tests/integration/test_rls.py` — papel sem bypass, guarda de startup, política (leitura/escrita/`WITH CHECK`/sem contexto),
  FKs compostas, conexão única reutilizada, 16 threads concorrentes, escritores concorrentes.
* `tests/integration/test_migrations_legacy.py` — banco vazio (up/down/up), banco com dados legados (usuários preservados,
  linhas legadas órfãs e invisíveis, claim dry-run/apply, recusas), baseline congelado.
* A suíte inteira roda com o app sobre o **papel de runtime real** (a fixture `client` não sobrescreve mais `get_db`).
