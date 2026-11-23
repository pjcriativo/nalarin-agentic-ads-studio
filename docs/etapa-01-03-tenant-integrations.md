# Etapa 01.3 — Isolamento das integrações, credenciais, API keys, bots e operações

Branch `feat/etapa-01-03-tenant-integrations` (a partir de `5e7c8dd`). Continua `etapa-01-02-tenant-isolation.md` (seção 8 = o que esta etapa fecha).

> **Veredito.** Conexões Google/Meta/TikTok, tokens, API keys, bots, otimização, aprovações e auditorias de mutação estão isolados **por organização**
> (aplicação + RLS), e toda operação que pode gastar falha fechada sem aprovação humana persistida. **Não declaro "isolamento completo"**:
> restam caminhos que não são controle de acesso por tenant — URLs-capacidade de uploads, CDN público do R2, SSRF residual (DNS rebinding/redirects)
> e, em desenvolvimento (sem `RUNTIME_DATABASE_URL`), o `RESET ROLE` (seção 9). Ver seção 10.

## 1. Mudanças

| Área | Antes | Agora |
|---|---|---|
| Conexões `google/meta/tiktok_ads_connections` | por **usuário** (`user_id`) | `organization_id` + RLS forçado `tenant_isolation`; `UNIQUE(org, conta)`; tokens continuam **criptografados** e nunca saem em resposta |
| Vínculo org↔conta | inexistente | tabela **`ad_account_links`** (`pending`/`active`/`revoked`): OAuth só cria *candidatas*; uma conta só opera com link **ativo** + conexão selecionada, **ambos na organização do chamador** (senão 404, fail closed) |
| Usuário em 2 organizações | compartilhava contas | cada organização tem as **suas** conexões; nada é herdado (testado com `X-Organization-Id`) |
| OAuth state | JWT sem uso único; cookie opcional | linha server-side `oauth_states` (usuário+organização+provider+hash do cookie), **uso único** (`UPDATE … WHERE used_at IS NULL`, consumido **antes** da troca do code), 10 min, cookie **obrigatório** (`OAUTH_REQUIRE_STATE_COOKIE`), mensagem única (sem oráculo); no callback o usuário é **reautorizado** (membership ativa + `connections:manage`), senão 403 + auditoria |
| MCC (Google) | `GOOGLE_ADS_LOGIN_CUSTOMER_ID` global (de todos) | `login_customer_id` **por organização** (deve ser conta da própria concessão); **sem fallback** global; seleção de conta individual por `POST /connection/select` |
| `api_keys` | por usuário, auth lia a tabela sem organização | `organization_id` + RLS cuja política também libera **só a linha cujo hash a sessão apresenta** (`app.api_key_hash`); chave sem organização **não autentica** (403); organização inativa/ chave revogada → 401; só `ads:read`/`ads:draft` (`ads:publish`/`ads:spend` **nunca** existem para máquina); após autenticar, a sessão é vinculada à organização da chave |
| Gestão de chaves | só script | `POST/GET/DELETE /organizations/{org}/api-keys` (`apikeys:manage`, admin+); revogar um membro revoga as chaves que ele criou |
| `/bot/*` | lia dados do *dono* da chave | lê **a organização da chave**; único write: `POST /bot/approvals` (pedir aprovação; humano decide) |
| `mutation_audits` | por usuário | `organization_id` + RLS; guarda `api_key_id`/`approval_id` |
| `/optimization/*` | posse por usuário | organização + RBAC + conta com link ativo; novos `/approvals` |
| `/overview`, `/bot/*` | `build_overview(user_id)` | `build_overview(org_id)` (conexão **da organização**) |

## 2. Permissões

| Permissão | Significado | Papéis |
|---|---|---|
| `connections:read` | ver metadados de contas conectadas | analyst+ |
| `connections:manage` | conectar (OAuth), selecionar, definir MCC, desconectar | admin, owner |
| `apikeys:manage` | criar/listar/revogar chaves de máquina | admin, owner |
| `ads:read` | ver campanhas/performance/aprovações | viewer+ (e chave `ads:read`) |
| `ads:draft` | preparar o que **não gasta** (campanhas PAUSED/DISABLED, **pedir** aprovação) | manager+ (e chave `ads:draft`) |
| `ads:publish` | alterar estado vivo sem aumentar gasto (pausar, negativar keywords) | admin, owner |
| `ads:spend` | **aprovar**/executar o que pode gastar | **owner** |

## 3. Gasto: aprovação humana, escopo de orçamento, idempotência (`app/services/approvals.py`)

Operações que gastam: **ativar campanha Google** (`/google-ads/campaigns/{id}/enable`) e **criar campanha/adset Meta com status ≠ PAUSED**.
(Anúncios Meta ACTIVE: recusados sempre. TikTok: só criação DISABLED — não há ativação exposta.)

1. `POST /optimization/approvals` (`ads:draft`, ou `POST /bot/approvals` com chave `ads:draft`) cria uma aprovação **pendente**, exigindo `idempotency_key` e conta com link ativo.
2. `POST /optimization/approvals/{id}/approve` — **humano** com `ads:spend` (uma chave Bearer não é sessão de usuário → 401), informando o **escopo de orçamento** (`max_budget_micros` ≥ pedido).
3. O endpoint executor **consome** a aprovação num `UPDATE … WHERE status='approved' AND expires_at>now() AND provider/conta/ação/hash do payload batem AND max_budget_micros ≥ orçamento real … RETURNING` (atômico: 8 threads → 1 consome). O orçamento real do Google é **lido** da conta antes (não declarado pelo cliente); se não der para verificar → 502, nada consumido.
4. Resultado gravado na linha; repetir o pedido com a mesma aprovação devolve o resultado (`idempotent_replay`) **sem gastar de novo**. Falha da plataforma **depois** do consumo → a aprovação fica consumida e o retry vira 409 ("resultado desconhecido: confira a plataforma e peça nova aprovação") — nunca retry cego.
5. Tudo em `mutation_audits` (pedido, decisão, recusas `approval-refused:…`, execução) + `audit_logs`.

Sem `approval_id` válido, sem orçamento explícito, sem permissão ou com kill-switch → **nada é executado** (testado com mocks que provam `enable`/`create` não chamados). Meta: orçamento em centavos é convertido a micros (×10 000).

## 4. Endpoints protegidos

`/google-ads/*`, `/tiktok-ads/*`, `/facebook/*` (OAuth, conexões, leituras, criações, uploads, `/ads/save`…), `/overview`, `/bot/*`, `/optimization/*`, `/organizations/{id}/api-keys`.
`/facebook/*`: `ad_account_id` do pedido tem de ser conta **vinculada à organização**; ids de campanha/adset recebidos são verificados contra a conta dona (`object_account_id`); `upload-*` valida a referência de mídia.

## 5. Login PostgreSQL dedicado / modo seguro

* `python -m app.scripts.provision_runtime_login` cria `ads_app_login` (`LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB`, membro **só** de `ads_app`); senha vem de `RUNTIME_DB_PASSWORD` (nunca argv/log).
* A API usa `RUNTIME_DATABASE_URL` (sem `SET ROLE`; `RESET ROLE` não leva a lugar nenhum — testado); `DATABASE_URL` (dono) fica para migrations/operador.
* **Produção exige** (`APP_ENV=production` ou qualquer `RAILWAY_ENVIRONMENT`): sem `RUNTIME_DATABASE_URL`, ou com login privilegiado/`CREATEROLE`, ou membro de papel privilegiado ⇒ **a API não sobe** (`verify_runtime_role_is_safe`).
* ⚠ **Pré-requisito de deploy**: provisionar o login e setar `RUNTIME_DATABASE_URL` **antes** de publicar; caso contrário a API recusa iniciar (falha fechada proposital).

## 6. Dados legados — quarentena e reconciliação (sem claim automático)

A migration `b8c9d0e1f2a3` mantém **todas** as linhas (tokens criptografados, vínculos usuário↔conta, hashes de chaves, auditorias) com `organization_id = NULL`: invisíveis a todas as organizações; chave legada não autentica; `CHECK … NOT VALID` bloqueia novas sem dono.
Efeito esperado: **todo usuário precisa reconectar ou ter suas conexões reivindicadas** antes de ver contas. Ferramenta de operador (privilegiada, dry-run por padrão, transacional):

```bash
python -m app.scripts.reconcile_legacy_integrations report
python -m app.scripts.reconcile_legacy_integrations claim-connections --org-id ORG --user-id USER [--apply]
python -m app.scripts.reconcile_legacy_integrations claim-api-keys --org-id ORG --keys ID1,ID2 [--apply]
python -m app.scripts.reconcile_legacy_integrations claim-audits --org-id ORG --user-id USER [--apply]
```
Recusa (sem alterar nada) se: organização inexistente/inativa; usuário não é membro **ativo** ou não é admin/owner; conta já vinculada/conectada na organização; chave sem criador. Cria os `ad_account_links` (`active` para a conta que estava selecionada, `pending` para as demais) e remove `ads:publish`/`ads:spend` das chaves.

## 7. Evidências (testes)

* `tests/unit/test_tenant_integrations.py` — **83 testes**: usuários em várias organizações e contas distintas; acesso cruzado a conexões/tokens/chaves/bots/auditorias; RBAC por papel para `connections:*` e `ads:read|draft|publish|spend`; OAuth **válido, inválido, expirado, repetido (replay), sem cookie, outro browser, org trocada (membership perdida / papel rebaixado), provider trocado, 8 callbacks concorrentes → 1 vence**; MCC por organização sem fallback; aprovação sem/pendente/expirada/rejeitada/de outra organização/payload diferente/orçamento acima do escopo/falha da plataforma; execução concorrente (1 de 8); idempotência; bot não aprova; chaves (mostrar uma vez, hash, escopos, revogação, org inativa, legada, RLS por hash); modo seguro (produção sem login ⇒ recusa; login provisionado sem escape; membro de papel privilegiado ⇒ recusa); SSRF (loopback, privados, metadata, IPv6, credenciais na URL, host que resolve a IP privado).
* `tests/integration/test_migrations_legacy.py` — linhas legadas de conexões/chaves/auditorias: preservadas byte a byte, invisíveis, `CHECK`, claim em dry-run → apply, atomicidade, recusas.
* Suíte completa: **432 passed**; frontend (lint 0 erros, 96 unit, i18n, build) e segurança (bandit, pip-audit, npm audit) sem achados. Nenhuma chamada real a Google/Meta/TikTok; nenhuma migration em dev/prod.

## 8. Mudanças visíveis ao frontend (sem alterar layout/i18n)

Contratos preservados; mudam as **permissões**: conectar/selecionar/desconectar contas exige admin+; `GET /*/connection(s)` exige analyst+ (viewer recebe 403); pausar exige admin+; ativar exige owner **e** aprovação (a UI atual envia `confirm` sem `approval_id` ⇒ 403 "requires a human-approved approval_id" até existir o fluxo de aprovação na UI).

## 9. Riscos remanescentes

| Risco | Estado |
|---|---|
| **SSRF em URLs externas** (`/facebook/upload-image|video`) | Guarda best-effort: só http(s), host que resolve **somente** a IPs públicos (bloqueia loopback/privados/link-local/metadata/IPv6). **Não cobre** DNS rebinding entre a checagem e o fetch nem redirecionamentos seguidos pela biblioteca; exige *egress filtering* de rede. Meta/TikTok/Google buscam as URLs também do lado deles. |
| **Acesso a arquivos privados** | Uploads locais são capacidade: quem tem a URL assinada vê o arquivo; sem listagem cross-tenant, mas URL vazada = acesso. Objetos R2 (`R2_PUBLIC_URL`) são **CDN público por desenho** (chaves por organização, sem controle de acesso). Arquivos legados soltos em `uploads/` não são servidos até realocação manual. |
| Dev sem `RUNTIME_DATABASE_URL` | conecta como dono + `SET ROLE`: SQL injetado pode `RESET ROLE`. Só produção/`REQUIRE_LOGIN_RUNTIME_ROLE` impedem. |
| Escopo de orçamento Google | verifica o `amount_micros` do orçamento da campanha; orçamentos **compartilhados** entre campanhas não são somados. Meta: confia no `daily_budget` do próprio payload (que é o que será enviado). |
| `/facebook/accounts` | lista as contas alcançáveis pelo token **da organização** (inclui não vinculadas, necessário para escolher); são contas da concessão da própria organização. |
| Cota da Ads Library | por organização (herdado da 01.2). |
| Escopos OAuth / revogação no provedor | desconectar revoga o vínculo local, **não** revoga o token no provedor. |
| Execução autônoma / jobs | continuam **desligados** (`AUTONOMOUS_EXECUTION_IMPLEMENTED = False`); nenhum job de otimização roda. Buscas agendadas e brand scrapes já rodam por organização. |
| Frontend não envia `X-Organization-Id` | usuário com várias organizações recebe 400 até existir o seletor. |
| Validação contra provedores reais | não executada (restrição): mocks cobrem o contrato; testar em conta sandbox antes de produção. |

## 10. Por que não digo "completo"

Enquanto existirem (a) URLs-capacidade e CDN público como forma de servir arquivo, (b) SSRF residual e (c) o modo de desenvolvimento com `SET ROLE`, há caminhos privados cuja proteção não é "acesso negado por tenant". Dados de negócio, conexões, tokens, chaves, bots, auditorias e operações de gasto **estão** isolados e testados.
