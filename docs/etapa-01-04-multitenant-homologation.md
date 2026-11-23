# Etapa 01.4 — Homologação multi-tenant e interface

Branch `feat/etapa-01-04-multitenant-homologation` (a partir de `421d98f`). Continua `etapa-01-03-tenant-integrations.md`.

> **Veredito: a plataforma NÃO está declarada pronta para produção.** A integração front/back do multi-tenant está concluída e testada
> (unitário, integração, e2e em navegador real, regressão visual, banco com login runtime sem privilégio), mas **nenhum serviço externo
> (Google Ads, Meta, TikTok, R2, Gemini/Fal) foi homologado de verdade**: tudo foi exercitado com mocks/fakes (restrição desta etapa) e há
> pré-requisitos de infraestrutura (seção 6) e riscos residuais (seção 8).

## 1. O que mudou na interface (design aprovado preservado)

| Item | Implementação |
|---|---|
| Seletor de organização | `OrganizationSwitcher` no cabeçalho (canto superior direito no desktop, barra superior no mobile). Mesmo "chip" âmbar/branco do `LanguageSwitcher`. Lista organizações com o **papel concedido pelo servidor**, teclado (↑ ↓ Enter Esc), `aria-*`, alvo ≥ 40 px no mobile. Mesmo com 1 organização o chip mostra onde você está. |
| `X-Organization-Id` automático | `lib/tenant.js` (estado do módulo) + `authFetch` (fetch), interceptor axios (`lib/http.js`, só para a **nossa** API) e `lib/facebookApi.js`. O header é lido **no momento da chamada**, então o *retry* após refresh de sessão leva a organização atual. |
| Persistência e validação | Seleção salva **por usuário** (`tenant:org:<userId>`), apenas como dica: `GET /organizations` revalida no carregamento, ao focar a aba e quando uma resposta diz "sem acesso à organização". Organização que sumiu ⇒ seleção limpa + aviso + troca para outra. Logout **e** novo login limpam seleção e preferências por-tenant. |
| Sem vazamento entre tenants | `TenantBoundary` **remonta** toda a árvore (providers, listas, formulários, caches) a cada troca de usuário/organização (`key=user:org`). Respostas que chegam depois da troca são **descartadas** (`epoch`: fetch, axios e facebookApi). Preferências locais (`lastSelectedAdAccountId`, rascunhos de campanha, URLs padrão…) passaram para `tenantStorage` (namespace usuário+organização). |
| Aprovação humana de gasto (Google Ads) | "Enable" agora abre `SpendApprovalModal` (pedido → aprovação com **teto de orçamento** → ativação com `approval_id`), com estados *none / pendente / aprovado / recusado / expirado / já utilizado*, `idempotency_key` por tentativa (nova chave após estado terminal) e painel `SpendApprovalsPanel` com as solicitações da organização. A ativação só é enviada com aprovação `approved` na tela; o servidor continua exigindo aprovação válida. |
| Permissões reais por papel | Servidor: `GET /organizations` devolve `permissions` do papel; `GET /organizations/role-matrix` devolve a matriz `ROLE_PERMISSIONS` verbatim. UI: `OrganizationPermissionsModal` (owner/admin/manager/analyst/viewer, papel atual destacado, aviso de que a UI não substitui o servidor) e botões que seguem as permissões (conectar/selecionar/MCC = `connections:manage`; criar = `ads:draft`; pausar = `ads:publish`; aprovar/ativar = `ads:spend`). |
| Estados 400/401/403/404, reconexão e quarentena | 401 ⇒ refresh → logout. 400 "várias organizações"/403 "sem acesso" ⇒ revalidação. 403 de papel ⇒ mensagem traduzida (`errors:backend.insufficientPermission`); página Google mostra bloco "sem permissão para ver conexões" ao papel sem `connections:read`; 404 de aprovação/conta ⇒ erro + recarrega o estado real; 409 ⇒ "já decidida/usada" + recarrega. Aviso "contas conectadas antes das organizações precisam ser reconectadas/atribuídas" quando não há conexão (dados legados ficam em quarentena no servidor). Tela dedicada para "sem organização" / "erro ao carregar" (com sair/tentar de novo). |
| i18n | 100 % dos textos novos em PT-BR e EN-US (`tenant.json`, `approvals.json`, trechos em `campaigns.json` e `errors.json`); `npm run test:i18n`: paridade, uso estático e *route sweep* nos dois idiomas. |

Bugs preexistentes corrigidos no caminho (necessários para "header após refresh"): `src/api/research.js` (axios) **não enviava `Authorization`**; `fetchUser` após refresh usava o **token expirado** (a sessão quebrava após 30 min); CORS não permitia `X-Organization-Id` (o navegador bloquearia todas as chamadas entre origens).

## 2. Backend (contratos que a UI usa)

* `GET /organizations` + `permissions`; `GET /organizations/role-matrix`; `payload_hash` nas aprovações (o front calcula o mesmo SHA-256 canônico para achar a aprovação de uma campanha — vetor de teste idêntico nos dois lados).
* CORS: `X-Organization-Id` em `allow_headers`.
* Middleware de segurança não sobrescreve mais `Content-Security-Policy`/`Referrer-Policy` definidos por uma rota.

## 3. Homologação do PostgreSQL com login runtime não privilegiado

* `tests/conftest.py` provisiona o login (mesmo código de `provision_runtime_login`) quando `RUNTIME_DATABASE_URL` está definido; `scripts/homologate-secure-db.sh` roda a suíte no banco descartável com `APP_ENV=production` + login dedicado.
* **Evidência local:** suíte completa no modo seguro = **445 passed, 3 skipped** (os 3 são testes que só fazem sentido no modo de desenvolvimento). Modo normal: **448 passed**.
* **CI:** novo passo no job `backend-tests` roda isolamento de tenant (integration + multitenancy + isolation + integrations + homologation) no modo seguro.
* `_set_runtime_role` passou a ser explícito: em modo seguro **não há `SET ROLE` nem `RESET ROLE`** no caminho da aplicação.

## 4. Testes executados

| Camada | Resultado |
|---|---|
| Backend (Postgres descartável, migrations aplicadas pela suíte) | **448 passed** (novos: `test_homologation_0104.py` — CORS, contratos, uploads privados, SSRF) |
| Backend em modo seguro (login dedicado, `APP_ENV=production`) | **445 passed, 3 skipped** |
| Frontend unitário/integração (vitest) | **148 passed** (eram 96). Novos: `tenant.test.js`, `multitenant.test.jsx` (provedores reais × backend fake com regras de tenant), `GoogleAdsCampaigns.approvals.test.jsx` |
| i18n | OK: 16 namespaces, paridade e route sweep PT-BR/EN-US |
| Lint / build | 0 erros (26 warnings = linha de base); build OK |
| **E2E em navegador real** (`tests/e2e/multitenant.e2e.mjs`, Edge headless, API fake por interceptação) | **38/38 em en-US e 38/38 em pt-BR** |
| Regressão visual (referência `421d98f` × novo, 1440/820/390 px, 19 rotas) | **41 pares com 0 px de diferença**; 16 diferenças, **todas no cabeçalho mobile (390 px)** — bbox x 80–262 × y 14–41, 1 134 px: o bloco logo/nome se desloca ~22 px porque o seletor entrou na barra. `/google-ads`: diferença só onde entraram o aviso de reconexão e o painel de aprovações (intencional). |
| Segurança | bandit (nada), pip-audit (nada), npm audit (nada) |

Cenários cobertos (usuário A com 2 organizações — dono em uma, *viewer* em outra — e usuário B com 1): troca e persistência, reload, logout, trocar de usuário no mesmo navegador (seleção do outro usuário ignorada), revogação de membership com a aba aberta, resposta lenta do tenant anterior chegando depois da troca (descartada), A→B→A rápido, refresh de sessão com header, RBAC por papel nos botões da página Google, matriz de permissões, pedido/aprovação/recusa/expiração/reutilização de `approval_id`, conflito 409 e aprovação desaparecida 404, foco preso no diálogo/Esc, PT-BR sem chaves cruas.

**Nenhuma operação Google Ads com gasto ocorre sem aprovação válida** — provado em três níveis: (1) UI: clicar em *Enable* só abre o diálogo (nenhuma chamada a `/enable`; teste e e2e); (2) UI: papéis sem `ads:draft`/`ads:spend` não têm os controles; (3) servidor (01.3): `enable` exige aprovação aprovada, dentro do escopo, de uso único (testes de backend); o fake dos testes front reproduz essa regra e o teste força a chamada direta e recebe 403.

## 5. Auditoria: SSRF, arquivos privados, URLs públicas

| Superfície | Achado | Ação nesta etapa |
|---|---|---|
| `fetch` de URL externa em `facebook_service.upload_image/video` (usavam `requests.get` seguindo redirects, sem limite) | SSRF por redirect: URL pública podia ricochetear para `169.254.169.254`/rede interna; sem teto de tamanho; extensão vinda da URL | **Corrigido:** `fetch_public_to_file` — a URL e **cada salto** (redirects seguidos à mão, máx. 3) passam por `assert_public_http_url`; `allow_redirects=False`; streaming com teto (10 MB imagem / 500 MB vídeo); extensão por allow-list. |
| Download da imagem gerada (`generated_ads.download_and_save_image`) e mídia do *brand scraper* (`follow_redirects=True`) | idem | **Corrigido:** `fetch_public_bytes` (mesma guarda, 20 MB / 100 MB). |
| `/uploads/<org>/<nome assinado>` | resposta herdava CSP/Referrer genéricos; URL-capacidade podia vazar por `Referer` | **Corrigido:** `CSP: default-src 'none'; sandbox`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private`. Middleware não sobrescreve mais. |
| Mesma URL de upload usada por outro tenant / adivinhada / extensão trocada | — | Já 404 (01.2); teste novo cobre. |
| Arquivos legados soltos em `uploads/` | quarentena (404) | mantido; realocação segue manual. |
| Objetos R2 em `R2_PUBLIC_URL` | **públicos por desenho** (CDN) | **não alterado**: chaves por organização, sem controle de acesso. |

**Riscos remanescentes (não corrigidos, por não serem seguros/compatíveis aqui):**
1. **DNS rebinding** entre a checagem e a conexão do `requests`/`httpx` (a guarda resolve o nome, a biblioteca resolve de novo). Exige *egress filtering* de rede (ou conexão fixada ao IP validado, que quebraria TLS/SNI sem camada própria).
2. Meta/TikTok/Google **buscam as URLs do lado deles** quando recebem a referência — a guarda local não protege isso (são serviços de terceiros).
3. URLs de upload local são **capacidades**: quem tem o link vê o arquivo. Sem expiração/revogação por arquivo.
4. R2 público (acima).
5. A guarda aceita `http://` (não só `https://`) para manter compatibilidade com CDNs existentes.

## 6. Configuração OBRIGATÓRIA para produção (documentada, **não alterada**)

1. **PostgreSQL ≥ 15** com `pgcrypto` (migration `f1a2b3c4d5e6`).
2. Provisionar o login runtime (uma vez por ambiente, com a conexão do dono):
   `ADMIN_DATABASE_URL=postgresql://<dono>… RUNTIME_DB_PASSWORD='<segredo forte ≥16>' python -m app.scripts.provision_runtime_login --login-role ads_app_login`
3. Variáveis do serviço da API:
   * `DATABASE_URL` = conexão do **dono** (migrations/Alembic e ferramentas de operador);
   * `RUNTIME_DATABASE_URL` = `postgresql://ads_app_login:<senha>@host:porta/db` (**sem** privilégios);
   * `APP_ENV=production` (ou qualquer `RAILWAY_ENVIRONMENT`) ⇒ a API **recusa iniciar** sem o login dedicado, com login privilegiado/`CREATEROLE` ou membro de papel privilegiado;
   * `SECRET_KEY` (≥32) e, se rotacionar, `TENANT_CONTEXT_KEY`/`python -m app.scripts.sync_tenant_key` (a API detecta chave divergente no startup);
   * `OAUTH_TOKEN_ENCRYPTION_KEY`, `ALLOWED_ORIGINS` (inclui a origem do frontend; **o header `X-Organization-Id` já é permitido**), `FRONTEND_URL`, `OAUTH_REQUIRE_STATE_COOKIE=true` (padrão) com HTTPS.
4. Ordem de implantação: migrations (dono) → provisionar login → definir `RUNTIME_DATABASE_URL`/`APP_ENV` → subir a API → verificar log de startup e `/health`.
5. Pós-deploy: usuários precisam reconectar contas ou o operador atribui o legado (`reconcile_legacy_integrations`, `claim_legacy_data`); sem isso as listas aparecem vazias por design.
6. **Frontend**: o build não muda de configuração (`VITE_API_URL`). Papéis: conectar contas = admin/owner; aprovar/ativar gasto = **owner**.

## 7. Como reproduzir

```bash
bash scripts/test-backend.sh                    # suíte backend (banco descartável)
bash scripts/homologate-secure-db.sh            # idem no modo seguro (login dedicado, APP_ENV=production)
cd frontend && npm run lint && npm run test:unit && npm run test:i18n && npm run build
# e2e/regressão visual (navegador local + puppeteer-core instalados FORA do repositório):
PUPPETEER_DIR="$TEMP/vr" APP_URL=http://localhost:5173 LANG_UI=pt-BR node tests/e2e/multitenant.e2e.mjs
```

## 8. Pendências e riscos

| Item | Estado |
|---|---|
| **Homologação real dos serviços externos** | **Não feita** (Google Ads, Meta, TikTok, R2, Gemini/Fal): sem credenciais reais por restrição. Executar em contas sandbox/teste antes de produção, principalmente o fluxo OAuth com `state` de uso único, MCC por organização e a leitura do orçamento da campanha antes de ativar. |
| UI do fluxo de aprovação para **Meta** (criação ACTIVE) e TikTok | o backend exige aprovação (Meta) / não expõe ativação (TikTok); só o Google tem a UI. |
| UI de gestão de membros, chaves de API, log de auditoria e reivindicação de dados legados | endpoints existem; sem telas (fora do escopo desta etapa). |
| Criação de organização na UI | endpoint existe; sem tela. |
| Cabeçalho mobile | o bloco logo/nome se desloca ~22 px (efeito colateral de adicionar o seletor). |
| Seleção padrão com várias organizações e nenhuma salva | usa a primeira da lista (o usuário vê e pode trocar). |
| Revalidação de membership | no carregamento, no foco da aba (throttle 15 s) e quando uma resposta diz "sem acesso"; não há *push* do servidor. |
| `OrganizationContext` depende de `GET /organizations` a cada login | se falhar, a tela de erro bloqueia o app (falha fechada) com "tentar de novo". |
| e2e em navegador | roda localmente (precisa de navegador + puppeteer-core fora do repo); no CI rodam apenas os testes vitest e os testes backend. |
