# Etapa 01.2 — Isolamento dos dados de negócio entre organizações

Branch `feat/etapa-01-02-tenant-isolation` (a partir de `9c59346`). Continua `docs/etapa-01-01-multitenant-core.md`.

> **Veredito: o isolamento dos DADOS DE NEGÓCIO está concluído; o isolamento TOTAL da plataforma NÃO.**
> Restam caminhos por **usuário** (não por organização) — conexões Google/Meta/TikTok, API keys, `/optimization`, `/bot`,
> `/google-ads`, `/tiktok-ads` e `mutation_audits` (seção 8) — e por isso esta etapa **não declara isolamento completo**.

## 1. Classificação das tabelas

| Classe | Tabelas | Regra |
|---|---|---|
| **Privada (tenant)** — `organization_id` + RLS forçado `tenant_isolation` | `brands`, `products`, `customer_profiles`, `brand_profiles` *(01.1)*; **novas:** `generated_ads`, `winning_ads` (templates), `facebook_campaigns`, `facebook_adsets`, `facebook_ads`, `verticals`, `facebook_pages`, `saved_searches`, `scraped_ads`, `search_logs`, `api_usage_logs`, `page_blacklist`, `keyword_blacklist`, `brand_scrapes`, `brand_scraped_ads` | Só a própria organização lê/escreve. Sem contexto válido → nenhuma linha. |
| **Catálogo global** — RLS forçado, leitura livre, escrita só com capacidade de administrador de plataforma assinada | `prompts`, `ad_styles` | Tenants só **leem**. `POST/PUT/DELETE` exigem `is_superuser` (`require_platform_admin`); a sessão admin não tem organização, logo continua sem enxergar dado de tenant. |
| **Auditoria** — append-only | `audit_logs` | `INSERT` + `SELECT` na própria organização; `UPDATE/DELETE/TRUNCATE` revogados do papel da aplicação. |
| **Controle** (sem RLS, por desenho) | `organizations`, `organization_members`, `users`, `roles`, `permissions`, `refresh_tokens` | Toda query filtrada por memberships do usuário. |
| **Ainda por usuário** (próxima etapa) | `google_ads_connections`, `meta_ads_connections`, `tiktok_ads_connections`, `api_keys`, `mutation_audits` | Ver seção 8. |
| Segredo | `tenant_ctx_secret` | Sem nenhum privilégio para o papel da aplicação. |

## 2. Migrations (cadeia: `e3f1a7c2d9b4` → `f1a2b3c4d5e6` → `a7b8c9d0e1f2`)

* `f1a2b3c4d5e6` — contexto assinado (seção 5), `audit_logs`, políticas antigas apontadas para o contexto assinado.
* `a7b8c9d0e1f2` — `organization_id` (FK `RESTRICT`, índice, `CHECK … NOT VALID`) + RLS nas 15 tabelas novas; `UNIQUE (id, organization_id)`
  nos alvos; **FKs compostas** (`generated_ads → brands/products/winning_ads`, `facebook_adsets → campaigns`, `facebook_ads → adsets`,
  `saved_searches/search_logs/facebook_pages → verticals`, `scraped_ads → saved_searches/facebook_pages`, `brand_scraped_ads → brand_scrapes`).
  Exige **PostgreSQL ≥ 15** (`ON DELETE SET NULL (coluna)` preserva a organização da linha; a migration falha com mensagem clara em versão menor).
* Unicidade global virou **por organização** (`scraped_ads.content_hash/external_id`, `facebook_pages.page_name`, `page_blacklist.page_name`,
  `keyword_blacklist.keyword`, `verticals.name`) — senão um tenant poderia sondar/bloquear outro por erro de unique.
* Downgrade testado (up/down/up). O downgrade de unicidade global falha se duas organizações já tiverem o mesmo valor (parada deliberada).

## 3. Endpoints protegidos (todos com `get_current_org` + RBAC + filtro `organization_id` + RLS)

| Router | Endpoints | Permissão (org) |
|---|---|---|
| `generated-ads` | `POST /generate-image`, `GET /`, `DELETE /{id}`, `POST /export-csv`, `POST /batch` | `ads:write` / `ads:read` / `ads:delete` / `ads:read` / `ads:write` |
| `templates` | `GET /`, `/filters`, `/{id}/preview`, `POST /upload` | `templates:read` / `templates:write` |
| `ad-remix` | `/deconstruct`, `/reconstruct`, `/blueprints[/{id}]` | `templates:write` / `brands:read`+`templates:read` / `templates:read` |
| `research` (23 rotas) | busca, buscas salvas, verticais, blacklists, páginas, uso de API, rate-limit, brand-scrapes, execução agendada | `research:read` / `:write` / `:delete` |
| `facebook` | `/campaigns/save`, `/adsets/save`, `/ads/save` (valida pais da organização) e as rotas de passagem p/ a Meta (`campaigns:read/write`) | `campaigns:*` |
| `dashboard` | `/stats` (todos os contadores por organização) | membro |
| `overview` | `GET ""` | `reports:read` (analyst+) |
| `copy-generation` | `/generate`, `/regenerate-field` | `ads:write` |
| `uploads` | `POST /` + `GET /uploads/{org}/{arquivo}` | `uploads:write` (manager+) |
| `prompts`, `ad-styles` | leitura: qualquer autenticado; escrita: administrador de plataforma | catálogo |
| `organizations` | `GET /{org}/audit-log` (novo) | `audit:read` (admin+) |

Papéis (novas permissões): *viewer* `ads/templates/campaigns/research:read`; *analyst* + `research:write`; *manager* + `ads/templates/campaigns:write|delete`,
`research:delete`, `uploads:write`; *admin* + `audit:read`.

Outros fechamentos: `overview` **não usa mais** o `FACEBOOK_ACCESS_TOKEN` global como fallback (mostrava a conta do servidor a quem não tinha conexão);
`/facebook/upload-image|video` só aceita URL http(s) ou arquivo da **própria** organização (antes lia **qualquer caminho do servidor**);
exportação CSV neutraliza injeção de fórmula e é limitada a 1000 ids; lotes limitados.

Serviços: `ResearchService`, `RateLimiter` (uso por organização), `SchedulerService`, `FacebookAdsLibraryAPI` (blacklists), `BrandScraperService`
(chaves R2 `orgs/<org>/brand-scrapes/…`; só apaga objetos do próprio prefixo) e as tarefas em background (`bind_context` na própria sessão).
`run_scheduled_searches.py` roda **uma sessão por organização ativa**.

## 4. Referências cruzadas

Dupla barreira: a API valida a propriedade de `brand_id`/`product_id`/`template_id`/`campaign`/`adset`/`vertical` (404 indistinguível de inexistente;
produto ↔ marca coerentes → 400) e o **banco** recusa via FK composta mesmo com bug de aplicação (testado inserindo via SQL).
Mídia: URLs `/uploads/<outra-org>/…` e chaves R2 de outra organização são recusadas ao gravar anúncios/produtos/marcas.

## 5. Endurecimento do contexto (`set_config`)

Problema: `ads_app` pode executar `set_config('app.current_org', …)`; SQL injetado trocaria de tenant.
Solução em camadas:

1. **Contexto assinado.** A aplicação grava `org | admin | nonce` + `HMAC-SHA256` (chave `TENANT_CONTEXT_KEY` ou derivada de `SECRET_KEY`).
   As políticas só chamam `app_current_org()` / `app_is_platform_admin()` — funções `SECURITY DEFINER` que recomputam a assinatura com a chave em
   `tenant_ctx_secret` (ilegível para `ads_app`). Setting sobrescrito/forjado/reproduzido ⇒ **nenhuma linha**, não "outro tenant". Nonce novo por transação.
2. **Validação de formato** do id de organização antes de qualquer SQL; parâmetros sempre *bind*.
3. **`RUNTIME_DATABASE_URL`** (recomendado em produção): login dedicado e sem privilégio. Sem ele, a conexão é do dono com `SET ROLE ads_app`, e SQL
   arbitrário ainda poderia dar `RESET ROLE` — o que a assinatura **não** impede. `REQUIRE_LOGIN_RUNTIME_ROLE=true` faz a API recusar subir sem isso.
4. Defesa em profundidade mantida: filtros explícitos em toda query e `before_flush` que carimba/recusa organização.
5. Guarda de startup: RLS forçado em tenant + catálogos + auditoria; **aceita** a assinatura legítima e **rejeita** a forjada; chave divergente →
   erro acionável (`python -m app.scripts.sync_tenant_key`, com a conexão do dono).

## 6. Uploads e arquivos privados

Arquivos em `uploads/<org>/<uuid>_<assinatura>.<ext>`; a assinatura (HMAC por organização+uuid) está **no nome** (a UI reconhece vídeo pela extensão; funciona em `<img>`).
O `StaticFiles` público foi **removido**; `GET /uploads/<org>/<nome>` só serve nome válido para aquela organização (forjado, outra org, traversal ⇒ mesmo 404).
O link é uma *capability*: quem o possui vê aquele arquivo. Arquivos **legados** soltos em `uploads/` não têm dono → quarentena: **não são servidos**.
Recurso público intencional: objetos R2 em `R2_PUBLIC_URL` (CDN público; chaves agora `orgs/<org>/…`, não controladas por acesso).

## 7. Auditoria

`audit_logs` registra: `member.added`, `member.role_changed`, `member.revoked`/`member.left`, `org.created`, `access.denied` (organização alheia/sem membership),
`permission.denied` (RBAC) e `catalog.write_denied`. Negações usam transação independente (sobrevivem ao erro HTTP). `detail` passa por lista de bloqueio de chaves
(token/secret/password/key/authorization…) e limite de tamanho; nunca guarda headers, corpo ou JWT (testado). Leitura: `GET /organizations/{id}/audit-log` (admin/owner).

## 8. Pendências — próxima etapa (documentado à parte)

| Item | Estado atual | Risco |
|---|---|---|
| Conexões Google/Meta/TikTok (`*_ads_connections`) | por **usuário**; token OAuth criptografado | Usuário em 2+ organizações usa as mesmas contas em todas; sem `organization_id`/RLS. |
| `/facebook/*` passagem à Meta, `/google-ads/*`, `/tiktok-ads/*`, `/overview` | requer membership/RBAC, mas os dados vêm da conexão **do usuário** | idem; `google-ads`/`tiktok-ads` não receberam RBAC de organização (fora de escopo: "sem Google Ads"). |
| `api_keys` + `/bot/*` | chave presa a `created_by_user_id`; a autenticação lê a chave **antes** de haver organização (RLS exige lookup SECURITY DEFINER) | bot vê as conexões do dono, de qualquer organização. |
| `/optimization/*`, `optimization_scheduler`, `mutation_audits` | por usuário/chave; execução de mutações não é por organização | auditoria e execução sem tenant. |
| Arquivos legados soltos em `uploads/` | quarentena (não servidos) | precisam ser realocados por operador para `uploads/<org>/` após o claim. |

## 9. Dados legados (quarentena — sem claim automático, sem exclusão)

Linhas anteriores ficam com `organization_id = NULL`: invisíveis a todas as organizações; o `CHECK … NOT VALID` bloqueia **novas** sem dono. Ferramenta de operador (papel privilegiado):

```bash
python -m app.scripts.claim_legacy_data report                                   # contagem por tabela (19)
python -m app.scripts.claim_legacy_data claim --org-id ORG --brands all --profiles all --apply   # brands/products/profiles
python -m app.scripts.claim_legacy_data claim-tables --org-id ORG --tables winning_ads,generated_ads   # dry run; --apply grava
```
`claim-tables` processa pais antes dos filhos e, se qualquer FK/unique recusar, desfaz tudo (nada fica pela metade).

## 10. Riscos remanescentes

* Sem `RUNTIME_DATABASE_URL` o SQL injetado ainda pode escapar via `RESET ROLE` (mitigação: configurar o login dedicado + modo estrito).
* Ids escolhidos pelo cliente em campanhas/adsets/ads/anúncios (`camp_xxx`) são aceitos por slug; colisão com linha de outro tenant ⇒ erro genérico (oráculo desprezível, sem vazamento de conteúdo).
* `audit_logs.organization_id` de tentativas negadas vem do header enviado: um atacante pode poluir o log de uma organização alheia (ruído, não vazamento).
* O limite de 200 chamadas/59 min à Ads Library agora é **por organização** (uma organização não consome a cota das outras, mas a cota total da plataforma não é mais um teto único).
* `facebook` passagem e `upload-*` com URL http(s) externa: SSRF preexistente (não tratado aqui).
* O frontend não envia `X-Organization-Id` (restrição de layout): usuário com várias organizações recebe 400.

## 11. Testes

* `tests/unit/test_tenant_isolation_complete.py` — organizações A/B, papéis viewer/analyst/manager/admin, CRUD/listagens/buscas/exportação/upload/relatórios, IDOR, referências cruzadas (API e banco), catálogos, auditoria.
* `tests/integration/test_rls_signed_context.py` — contexto forjado/reproduzido/auto-concedido, segredo ilegível, todas as tabelas sem contexto, reuso de conexão, 16 threads, login dedicado sem escape de papel, guarda de chave.
* `tests/integration/test_migrations_legacy.py` — base vazia (up/down/up) e base legada com dados em todas as tabelas novas (quarentena, nada apagado, `claim-tables`).
* Suíte completa: ver relatório de entrega.
