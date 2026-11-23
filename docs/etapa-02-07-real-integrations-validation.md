# Etapa 02.7 — Homologação real das integrações

Branch: `feat/etapa-02-07-real-integrations-validation` (a partir de `feat/etapa-02-06-agents-dashboard`, `1b20abac`).

> ## Estado: **Fase A concluída. Fase B (chamadas reais) NÃO executada — aguardando autorização explícita.**
> Nenhuma chamada a Anthropic, Gemini, OpenAI ou Google Ads foi feita nesta etapa. Nenhum teste com mock é classificado como homologação.
> Resultado real de **todas** as integrações: **não testado** (seção 8). Nenhuma migration, deploy, merge, campanha ou mudança de frontend.

## 1. O que a Fase A entregou

| # | Item | Entrega |
|---|---|---|
| 1 | Jobs da 02.6 verdes | Ver seção 9 |
| 2 | Auditoria de configuração e contratos | Seção 4 (1 defeito de contrato corrigido, riscos documentados) |
| 3 | Testes de integração reais, separados dos mocks | `backend/tests_real/` (fora de `tests/`: nem `pytest` nem o CI os coletam; todos pulam sem autorização) |
| 4 | Ambiente isolado | `docker-compose.homologation.yml` (PostgreSQL em tmpfs `ads_homolog_test`, API, worker, frontend; **sem `env_file`**, segredos descartáveis, portas só em 127.0.0.1) + `scripts/homologation.sh` |
| 5 | Execução com allowlist, limite de custo, logs sem credenciais | `backend/app/homologation/` (`guard.py`, `operations.py`, `runner.py`, CLI `python -m app.homologation`) |
| 6 | Validação ponta a ponta UI → API → worker → Gateway → provedor | Seção 6 (camada por camada, com o que cada uma prova) |
| 7 | OAuth, conta, MCC, GAQL, Keyword Planner | Operações `gads.*` (somente leitura), seção 5 |
| 8 | Respostas estruturadas, tool-calling, tokens, preços, fallback | Operações `ai.*` + testes `tests_real` (fallback real com 2º provedor), seção 5 |
| 9 | Pré-requisitos de credenciais e permissões | Seção 3 |

## 2. Como o harness protege a Fase B

* **Autorização dupla**: `run` só envia algo com `--execute` **e** `HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B`. `plan` e `preflight` são offline.
* **Allowlist fechada** (`operations.py`): 11 operações de IA (verify gratuito, completion mínima, tool-calling com ida e volta, JSON estruturado — por provedor) e 6 de Google Ads, **todas leitura**. Um id fora da lista é rejeitado (`ValueError`); um teste estático impede qualquer `mutate`/criar/pausar/ativar no pacote.
* **Custo**: cada chamada paga é precificada **antes** (pior caso = prompt estimado + todo o `max_tokens`, com o **preço informado por você**); modelo sem preço ⇒ **não envia** (fail-closed). Tetos padrão: **US$ 0,05 por operação** e **US$ 0,50 no total** (`--max-op-usd`, `--max-total-usd`). Custo real desconhecido é cobrado pelo pior caso. Prompts fixos e neutros (sem dado de cliente), `max_tokens` 256.
* **Sem segredos**: chaves só por variável de ambiente; o relatório guarda status, contagens, tokens, custo e um detalhe curto, tudo passado por `Redactor` (valores exatos + padrões `sk-…`, `AIza…`, `ya29.…`, `1//…`, `Bearer …`). **Nunca** prompt, resposta, id de conta ou métrica.
* **Pré-requisito encadeado**: `gads.account_info/structure/performance/keyword_ideas` só rodam se `gads.oauth_refresh` passou (e `account_info` para as demais).
* **Banco**: os testes `tests_real` só rodam em PostgreSQL descartável (`tests/conftest.py` recusa banco que não termine em `_test`).

Cobertura offline do próprio harness (CI): `backend/tests/unit/test_homologation_0207.py` — 15 testes (autorização, allowlist, teto de custo sem envio, acúmulo do teto, relatório sem segredo, redação, assinatura Gemini, somente leitura). **Isso prova os guardas, não os provedores.**

## 3. Credenciais e acessos necessários (nomes, nunca valores)

Todas via variáveis de ambiente do shell que executa a Fase B (nunca em arquivo versionado). Sugestão: chaves **de teste**, projeto/conta dedicados, com limite de gasto no próprio provedor.

### Provedores de IA — para cada um de `ANTHROPIC`, `GEMINI`, `OPENAI`
| Variável | Para quê |
|---|---|
| `HOMOLOG_<P>_API_KEY` | chave de API (escopo mínimo; a Anthropic/OpenAI/Google precisam apenas de *inference* + *list models*) |
| `HOMOLOG_<P>_MODEL` | id do modelo a testar (**o sistema não tem modelo embutido**: você escolhe) |
| `HOMOLOG_<P>_INPUT_PRICE_MICROS_PER_MTOK` / `…_OUTPUT_PRICE_MICROS_PER_MTOK` | preço em micro-USD por milhão de tokens (US$ 3/Mtok = `3000000`), da tabela oficial **vigente** do provedor; sem eles nada pago é enviado |
Requisitos: faturamento ativo/crédito no provedor; o modelo precisa suportar **tool-calling** e **saída JSON**; para validar a ordem de fallback do E2E, informe **pelo menos 2 provedores**.

### Google Ads (somente leitura)
| Variável | Para quê |
|---|---|
| `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET` | cliente OAuth (tipo Web) do projeto Google Cloud, com a *Google Ads API* habilitada |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | token de desenvolvedor da conta **manager (MCC)**. **O nível importa**: *Test access* só lê **contas de teste**; *Explorer/Basic/Standard* leem contas reais; o Keyword Planner depende do nível |
| `HOMOLOG_GADS_REFRESH_TOKEN` | refresh token do usuário que autorizou o escopo `https://www.googleapis.com/auth/adwords` (obtido pelo fluxo OAuth do produto ou OAuth Playground) |
| `HOMOLOG_GADS_CUSTOMER_ID` | conta a ler (10 dígitos). **Preferir conta de teste ou conta sem campanhas ativas** |
| `HOMOLOG_GADS_LOGIN_CUSTOMER_ID` | (opcional) MCC por onde se acessa a conta cliente |
| `HOMOLOG_GADS_LANGUAGE` / `HOMOLOG_GADS_GEO_IDS` | (opcional) idioma e ids geográficos do Keyword Planner (padrão `en`, `2840` = EUA) |
Permissões no Google Ads: o usuário do refresh token precisa de acesso **Somente leitura** (ou superior) à conta e, se houver MCC, à conta manager. **Nenhuma permissão de escrita é necessária** — recomenda-se conceder só leitura.

### Opcionais do E2E de agentes
`HOMOLOG_PRODUCT_URL` (página pública para o `product_analyst`; sem ela fica "não testado"), `HOMOLOG_TASK_MAX_USD` (teto por tarefa, padrão 0,10).

## 4. Auditoria de configuração e contratos

Metodologia: leitura dos adaptadores (`app/ai/adapters/*`), gateway, preços, leitor Google Ads, OAuth, registro dos agentes e configuração; comparação com o formato de fio documentado de cada API. **Isto é auditoria de código, não prova de comportamento real.**

| # | Achado | Gravidade | Situação |
|---|---|---|---|
| A1 | **Gemini descartava `thoughtSignature`** das partes `functionCall`. Modelos com raciocínio devolvem a assinatura e o Gemini 3 **rejeita** o turno seguinte (tool_result) se ela não voltar. O runner reenvia os mesmos objetos `ToolCall`, então o campo se perdia no adaptador | Alta (quebraria agentes com tool-calling no Gemini) | **Corrigido**: `ToolCall.provider_data` (opaco) guarda e o adaptador reemite; 2 testes. **Confirmar na Fase B** (`ai.gemini.tool_call`, que faz a ida e volta) |
| A2 | OpenAI `response_format: json_object` exige a palavra "JSON" nas mensagens (senão 400). Os agentes não usam `json_mode` (validam o JSON do contrato de saída), então não é afetado hoje | Baixa | Documentado; a sonda de homologação inclui "JSON" no prompt |
| A3 | Anthropic: tokens de *cache creation* são cobrados ao preço de entrada (na realidade têm sobretaxa). O gateway **não envia `cache_control`**, então o cache não ocorre | Baixa (subestimaria só se cache for adotado) | Pendência registrada |
| A4 | Fallback só ocorre em `rate_limit`, `timeout`, `unavailable`. Modelo inexistente (`not_found`), chave inválida, cota esgotada e conteúdo bloqueado **param** sem fallback | Decisão de projeto, correta (não mascara erro de configuração) | Mantido; E2E real de fallback usa endpoint inalcançável do 1º provedor (erro `unavailable` genuíno) com 2º provedor real |
| A5 | `google-ads>=24.1.0` sem teto: o CI instala sempre o mais recente (local: 35.1.0). Versões da API Google Ads são descontinuadas periodicamente; uma atualização do SDK pode mudar comportamento sem mudança no repositório | Média | **Risco/pendência**: fixar versão após a Fase B confirmar a combinação SDK × API. Não alterado agora para não invalidar o CI |
| A6 | Sem nenhum preço de modelo embutido (por desenho). Um modelo sem preço com limite de gasto ativo falha **fechado** (`budget_exceeded`); sem limite ativo, o custo registrado fica `null` | Informativo | Correto; a homologação exige preços informados |
| A7 | OAuth: `access_type=offline` + `prompt=consent` (emite refresh token), escopo único `adwords`, `state` compartilhado (`oauth_state`), redirect configurável. Seleção de MCC por organização, sem fallback global (isolamento) | OK | Validar redirect URI **idêntico** ao cadastrado no Google Cloud (Fase B) |
| A8 | Leitor Google Ads: só `search_stream` (GAQL `SELECT`) e `generate_keyword_ideas`; consultas montadas só com constantes/datas/ids numéricos; limites por tarefa e por organização/hora; proveniência `real` × `estimate` | OK | Teste estático existente + novo no pacote de homologação |
| A9 | Contagem de tokens: Anthropic soma `input + cache_read + cache_creation`; OpenAI usa `prompt_tokens`/`completion_tokens` (raciocínio incluso na saída); Gemini soma `thoughtsTokenCount` à saída (a API o reporta fora de `candidatesTokenCount`) | OK pela documentação | **Confirmar com números reais** (`ai.*.chat_minimal`: tokens > 0 e custo calculado) |
| A10 | Anthropic `usage.output_tokens_details.thinking_tokens` (nome do campo de raciocínio) não pôde ser verificado offline; se ausente, `reasoning_tokens = 0` apenas no registro — o custo usa `output_tokens` e **não** é afetado | Baixa | Observar na Fase B |

## 5. Matriz de integrações e testes

Legenda: **Mock** = existe e passa no CI (não é homologação). **Real** = precisa da Fase B.

| Integração | Capacidade | Teste com mock (existente) | Teste real (preparado) | Resultado real |
|---|---|---|---|---|
| Anthropic | Chave aceita (`GET /v1/models`) | `test_ai_adapters`, `test_ai_credentials_api` | `ai.anthropic.verify` | **não testado** |
| Anthropic | Completion mínima, tokens, custo | `test_ai_adapters`, `test_ai_gateway` | `ai.anthropic.chat_minimal` | **não testado** |
| Anthropic | Tool-calling (ida e volta) | `test_ai_adapters`, `test_agents_gateway_integration` | `ai.anthropic.tool_call` | **não testado** |
| Anthropic | JSON estruturado | idem | `ai.anthropic.json_mode` | **não testado** |
| Gemini | verify / completion / tool-calling (c/ `thoughtSignature`) / JSON | idem + `test_homologation_0207` | `ai.gemini.verify`, `.chat_minimal`, `.tool_call`, `.json_mode` | **não testado** |
| OpenAI | verify / completion / tool-calling / JSON | idem | `ai.openai.verify`, `.chat_minimal`, `.tool_call`, `.json_mode` | **não testado** |
| Gateway | Roteamento por org, fallback, orçamento, contabilidade | `test_ai_gateway*`, `test_agents_gateway_integration` | `tests_real`: fallback real entre 2 provedores; orçamento bloqueia antes de enviar | **não testado** |
| Google Ads | OAuth refresh (escopo `adwords`) | `test_google_ads` | `gads.oauth_refresh` | **não testado** |
| Google Ads | Contas acessíveis / seleção de conta e MCC | `test_google_ads`, `test_tenant_integrations` | `gads.accessible_customers` | **não testado** |
| Google Ads | Conta (GAQL `customer`), estrutura, desempenho 7 dias | `test_agents_google_tools` | `gads.account_info`, `.structure`, `.performance` | **não testado** |
| Google Ads | Keyword Planner | `test_agents_google_tools` | `gads.keyword_ideas` | **não testado** |
| Agente `ad_copywriter` | sem dado externo | `test_agents_*` | `tests_real` por provedor | **não testado** |
| Agente `policy_reviewer` | sem dado externo | idem | `tests_real` por provedor | **não testado** |
| Agente `product_analyst` | página pública (web) | `test_agents_page_analysis` | `tests_real` c/ `HOMOLOG_PRODUCT_URL` | **não testado** |
| Agente `market_researcher` | Keyword Planner, termos, estrutura | `test_agents_google_tools` | `tests_real` c/ conexão Google real | **não testado** |
| Agente `search_ads_strategist` | estrutura, desempenho, Keyword Planner | idem | idem | **não testado** |
| Agente `performance_analyst` | métricas reais da conta | idem | idem | **não testado** |
| Supervisor | plano + consolidação | `test_agents_gateway_integration` | via tarefa automática (sem agente fixo) | **não testado** |

## 6. Validação ponta a ponta: o que cada camada prova

| Camada | Como é validada na Fase B | Observação |
|---|---|---|
| Provedor ↔ adaptador | `python -m app.homologation run --execute` (mesmos adaptadores da produção) | relatório JSON sem segredos |
| Gateway → provedor | `tests_real` usa o `AIGateway` real com credencial **criptografada no banco** (BYOK), modelo, preço, roteamento e orçamento da organização | prova roteamento/fallback/contabilidade reais |
| Worker → Gateway | `execute_task` (o mesmo código que o worker chama) sobre PostgreSQL descartável com RLS e papel de runtime | o processo `python -m app.worker` em si já foi exercido na 02.6 |
| API → worker | pilha isolada (`scripts/homologation.sh up`): a API enfileira e o worker executa | |
| Interface → API | com a pilha isolada no ar, a UI de Agentes/Central de IA cria a chave e a tarefa; roteiro E2E da 02.6 (`frontend/tests/e2e/agents-dashboard.e2e.mjs`) **precisa de um modo sem o gateway simulado** (hoje o `worker_launcher.py` o simula) | **Pendência**: a verificação visual ponta a ponta da UI com provedor real é manual/guiada na Fase B; o frontend **não foi alterado** |

Validação do próprio código dos testes reais (sem rede): rodei `tests_real` com a autorização simulada e um transporte HTTP falso local; `ad_copywriter` e o teste de orçamento passaram; o teste de `policy_reviewer` falhou **por defeito do meu falso** (ele devolveu a saída do copywriter), não do código. Isso apenas valida o encanamento — **não é homologação**.

## 7. Como executar a Fase B (após autorização)

```bash
# 1) offline: o que será executado e o que falta
scripts/homologation.sh plan
scripts/homologation.sh preflight

# 2) exportar as credenciais de TESTE (seção 3) e a autorização
export HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B
# 3) provedores + Google Ads (leitura) + agentes E2E em PostgreSQL descartável:
scripts/homologation.sh phase-b        # relatório em docs/evidence/etapa-02-07/provider-report.json
```
Para um provedor apenas: `python -m app.homologation run --execute --only ai.openai.verify ai.openai.chat_minimal`.
Custo máximo do `run`: US$ 0,50 (padrão) + os testes `tests_real` (≤ US$ 0,10 por tarefa).

## 8. Resultado por provedor (Fase B)

| Provedor | Resultado | Evidência |
|---|---|---|
| Anthropic | **não testado** (aguardando autorização e credenciais) | — |
| Gemini | **não testado** | — |
| OpenAI | **não testado** | — |
| Google Ads (OAuth, conta/MCC, GAQL, Keyword Planner) | **não testado** | — |
| Agentes (6) + supervisor | **não testado** com provedor real | — |

## 9. CI (GitHub Actions)

* **Base (02.6, commit `1b20abac`)**: run 12 do workflow *Test Suite* — `backend-tests`, `security-scan` e `frontend-tests` **success** (consultado pela API pública do GitHub). Nenhuma correção prévia foi necessária.
* **Suíte backend local desta etapa** (PostgreSQL descartável): **988 passed, 1 skipped, 0 failed**.
* **Esta branch**: ver o resultado dos três jobs do commit da etapa, registrado ao final (seção 9.1).

### 9.1 Jobs desta branch
(preenchido após o push)

## 10. Riscos e pendências

1. **Nada foi homologado com serviço real.** Todo o comportamento de provedores/Google Ads continua baseado em contrato documentado e dublês.
2. A1 (assinatura do Gemini) é corrigida por documentação oficial, mas só a Fase B confirma.
3. A5: fixar `google-ads` após validar a combinação SDK × versão da API; verificar o **nível do developer token** (teste × produção) antes de esperar dados reais.
4. Preços por modelo são **fornecidos por você** na Fase B (o produto não embute preços); preço desatualizado distorce o custo registrado.
5. UI ponta a ponta com provedor real é guiada/manual (seção 6), pois o E2E automatizado da 02.6 usa gateway simulado.
6. Teto de custo do harness é conservador (pior caso); o custo real tende a ser bem menor.
7. Limites de Google Ads por tarefa/hora e do Keyword Planner continuam os da 02.5; o Keyword Planner pode exigir nível de token superior ao de teste.
