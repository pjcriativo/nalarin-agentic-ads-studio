# Etapa 00 — Estabilização técnica

Branch: `chore/etapa-00-stabilization` (criada a partir de `chore/design-skills-director` @ `80d3f06`).
Escopo: estabilizar antes do multi-tenant. **Não** implementa multi-tenant, agentes nem módulos novos.

## 1. Divergência entre remotos e branches

Verificado com `git fetch origin` + `git fetch upstream`:

| Ref | Commit |
|---|---|
| `origin/main` | `c500cc5` |
| `upstream/main` | `c500cc5` |
| `main` (local) | `c500cc5` |
| `origin/feat/i18n-pt-br-en-us` | `9c1b500` |
| `origin/chore/design-skills-director` | `80d3f06` |

- `origin/main` e `upstream/main` são **idênticos** (merge-base = o próprio commit): sem divergência.
- A história é **linear**: `main` → `9c1b500` (i18n) → `4910b36` (skills) → `140a66a` (i18n erros) → `80d3f06` (dev config). Nenhum commit foi reescrito, descartado ou mesclado; todos preservados.
- O trabalho de i18n/skills ainda **não está em `main`** (4 commits à frente). Esta etapa não faz merge em `main`.

## 2. Falhas dos testes backend (11 → 0) e causas reais

| Falha | Causa real | Correção |
|---|---|---|
| `test_uploads` ×3 | Fixtures com bytes falsos; o sniff de magic-byte (commit `45e47ba`) corretamente os rejeita | Fixtures com assinaturas reais (código **não** foi afrouxado) |
| *(brecha achada)* | Vídeos **não** passavam por sniff: um `.exe` renomeado para `.mp4` era aceito | `_looks_like_video` (mp4/mov/webm/avi, assinatura × extensão) + leitura limitada a `max_size+1` + 6 testes novos |
| `test_bot_api` ×1 | Teste desatualizado (`/bot/spend` existe) | Lista de rotas atualizada; segue exigindo somente GET |
| `test_google_ads` ×7 | Código rodava `_require_configured()` **antes** do guard `confirm`, contra o contrato documentado (confirm primeiro) → 500 em vez de 400 | Ordem corrigida em 4 rotas; testes "404" passam a configurar o Google Ads explicitamente; 2 testes novos |
| `test_optimization` (regressão achada) | `PROPOSAL_TOKEN_SECRET=""` anulava o fallback para `SECRET_KEY` (`os.getenv(k, default)` com string vazia) | `os.getenv(a) or os.getenv(b)` |

## 3. Bugs reais encontrados e corrigidos (além dos 11)

- **Rotas de otimização duplamente prefixadas** (`/api/v1/optimization/optimization/*`): o painel do frontend nunca alcançava a API.
- **`/bot/spend` quebrava**: iterava as chaves do dict de `build_overview` (`{"campaigns","errors"}`) e o teste mascarava o erro com um mock de lista. Linhas do overview também não traziam `status` (campanhas ativas sempre 0).
- **`/bot/connections` vazava conexões de todos os usuários** a qualquer chave `ads:read` → agora restrito ao dono da chave; chave sem dono → 403.
- **Limites do auto-modo dependiam de números enviados pelo cliente** (`today_spend_micros`, `today_action_count`) → ignorados; contagem vem do audit log; gasto sem fonte confiável **falha fechado** (`daily-spend-unverifiable`) quando há teto.
- **`/propose` assinava token para qualquer conta** → exige que o usuário tenha a conta conectada (Google/Meta/TikTok), respeita o kill-switch e grava auditoria. `get_current_user` → `get_current_active_user`.
- **Fuso WIB (UTC+7) fixo** para a janela operacional → `ADS_AUTO_TIMEZONE` (padrão `America/Sao_Paulo`).
- **Teste `xfail` obsoleto** de upload sem auth (já exigia auth; estava `xpassed`).

## 4. Fluxos simulados desativados explicitamente

- `optimization_scheduler.py`: avaliava 2 campanhas **fictícias** (`act_12345`, `g_555`) com métricas inventadas e gravava auditoria. Agora é um no-op documentado (`status: disabled`, nunca lê/escreve dados). `AUTONOMOUS_EXECUTION_IMPLEMENTED = False` é a fonte de verdade.
- `GET /optimization/status` expõe `engine_active`; o painel não mostra mais "Auto-Mode ON" nem "24/7 Monitored" sem motor real e exibe aviso (PT-BR/EN-US). O veredito do "simular" diz que nada foi executado.
- `Settings`: a "geração de estilos por IA" esperava 2 s e dizia "N estilos gerados!" sem criar nada → agora informa que não está disponível.
- `Reporting`: números fixos + barras com `Math.random()` a cada render → barras determinísticas e **aviso visível de dados de exemplo**.

## 5. Dependências e segurança

| Item | Antes | Depois |
|---|---|---|
| `npm audit` | 12 em dependências de produção (1 crítica, 9 altas, 2 moderadas) + dezenas no tooling de dev | **0** (produção e dev) |
| `pip-audit` | 24 vulnerabilidades em 7 pacotes | **0** |
| `python-jose` (sem correção) | em uso | substituído por **PyJWT** |
| FastAPI / Starlette / python-multipart / dotenv / anyio | 0.104.1 / 0.27 / 0.0.6 / 1.0.0 / 3.7.1 | 0.165.0 / 1.10.0 / 0.0.34 / 1.2.4 / 4.16.0 |
| Bandit (`-ll -ii`) | — | exit 0 |
| Vitest | 1.x | 5.x (exige Node ≥ 22.12; `engines` + Dockerfile + CI em Node 22) |

Removido (comprovadamente sem uso — nenhum import em `src`/configs): `express`, `better-sqlite3`, `multer`, `adm-zip`, `cors`, `dotenv`, `concurrently`, `@google/generative-ai`, `@supabase/supabase-js`, `src/lib/supabase.js`, scripts `server`/`dev:all` (apontavam para `server.js` inexistente), `pnpm-lock.yaml` e `pnpm-workspace.yaml` (CI/Docker usam npm).

Hardening adicional: `SECRET_KEY` ≥ 32 caracteres (falha no startup); imagem do backend roda como **usuário não-root** (uid 10001) com `HEALTHCHECK`; nginx com `Permissions-Policy`, COOP e HSTS — e os headers agora também valem para **assets estáticos** (antes o `add_header` do location sobrescrevia os do servidor); credenciais não devem usar prefixo `VITE_` (docs corrigidas + `envLeak.test.js` como guarda).

## 6. Testes com PostgreSQL descartável

- `tests/conftest.py` **exige** `TEST_DATABASE_URL` (sem fallback para `DATABASE_URL`), recusa banco cujo nome não termine em `_test`, e neutraliza credenciais do `.env` do desenvolvedor antes de importar o app.
- O schema é criado com as **migrations Alembic reais** (`upgrade head`), não `create_all`. `alembic check`: *No new upgrade operations detected* (modelos == migrations, head único `a1d2e3f4b5c6`).
- `docker-compose.test.yml` (Postgres 16 em tmpfs, porta 55432) e `scripts/test-backend.sh` (sobe, testa, derruba).
- CI (`.github/workflows/test.yml`) reescrita: dispara em **qualquer branch** e PR; Postgres de serviço `ads_studio_test`; `OAUTH_TOKEN_ENCRYPTION_KEY` (chave Fernet de teste) e `SECRET_KEY` de teste; frontend roda lint, unit, i18n e build (o job anterior executava Playwright, inexistente no projeto); bandit, pip-audit e npm audit são **gates reais** (sem `|| true`).

## 7. Resultados (antes → depois)

| Verificação | Antes | Depois |
|---|---|---|
| Backend pytest | 166 ok / **11 falhas** / 1 xpassed | **206 ok / 0 falhas** / 0 xpassed |
| Cobertura backend | 50,93 % | 52,18 % |
| Frontend vitest | 88 ok | **96 ok** (6 arquivos) |
| `test:i18n` | ok (87) | ok (87), paridade PT-BR/EN-US |
| ESLint | 108 problemas (**82 erros**, 26 avisos) | **0 erros**, 26 avisos |
| Build frontend | ok | ok (6,2 s) |
| Regressão visual (3 viewports × 21 rotas) | — | 62/63 idênticas (0 px); só `/reporting` difere (aviso intencional) |
| Imagens Docker (backend, frontend) | — | build ok; backend uid 10001; `nginx -t` ok; headers conferidos em HTML e asset |

## 8. Pendências (não corrigidas nesta etapa)

1. **Multi-tenant** (próxima etapa, por restrição de escopo): `Brand`, `Product`, `CustomerProfile`, `GeneratedAd`, etc. continuam sem `user_id`/`organization_id`; qualquer usuário autenticado lê os dados de todos. Facebook segue com token global.
2. 26 avisos `react-hooks/exhaustive-deps` pré-existentes: corrigi-los altera o momento das buscas em telas aprovadas; mantidos como avisos.
3. Código **inalcançável** (sem rota/importador) que contém simulações: `Wizard`, `CopyBuilder`, `AnalyzeTemplatesStep`, `ImageAdWizard`, `AdCard`. Não removido para preservar o frontend aprovado; remover mediante aprovação.
4. `Reporting` ainda mostra dados de exemplo (agora rotulados). Ligar ao `/overview` é funcionalidade nova.
5. Fallbacks legados `VITE_FACEBOOK_*` / `VITE_GEMINI_API_KEY` no backend mantidos por compatibilidade com deploys existentes (documentação já recomenda os nomes sem `VITE_`).
6. `/uploads` (fallback local) é servido sem autenticação, por desenho (criativos precisam de URL pública para a Meta).
7. Não executados: e2e `agent-browser`, chamadas reais a Google/Meta/TikTok/Gemini/fal.ai, contra qualquer banco que não o descartável.
8. `datetime.utcnow()` (3 serviços) e o campo `copy` em `ImageGenerationRequest` ainda geram avisos de depreciação.

## 9. Antes de ir para produção (atenção)

- `SECRET_KEY` precisa ter ≥ 32 caracteres, senão o backend **não sobe** (fail-fast).
- A imagem do backend agora roda como não-root e em Python 3.12: confirme gravação em `uploads/` se não usar R2.
- Atualize `node_modules` local: pare o `npm run dev` e rode `npm ci` (Vitest 5 / Vite 7.3 exigem Node ≥ 22.12).
