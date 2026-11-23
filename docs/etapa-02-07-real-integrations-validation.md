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
Run `45237781296` (workflow *Test Suite*, commit `cc550c5`, esta branch), consultado pela API pública do GitHub:

| Job | Resultado | Concluído (UTC) |
|---|---|---|
| `backend-tests` | **success** | 2026-11-23 18:42:31 |
| `security-scan` | **success** | 2026-11-23 18:23:46 |
| `frontend-tests` | **success** | 2026-11-23 18:24:17 |

Run `45242361817` (commit `ecbfc84`, pré-voo): `security-scan`, `backend-tests` e `frontend-tests` **success**. O commit da correção final do pré-voo tem run próprio: ver 11.8.

Nenhuma falha; nenhuma correção necessária.

## 10. Riscos e pendências

1. **Nada foi homologado com serviço real.** Todo o comportamento de provedores/Google Ads continua baseado em contrato documentado e dublês.
2. A1 (assinatura do Gemini) é corrigida por documentação oficial, mas só a Fase B confirma.
3. A5: fixar `google-ads` após validar a combinação SDK × versão da API; verificar o **nível do developer token** (teste × produção) antes de esperar dados reais.
4. Preços por modelo são **fornecidos por você** na Fase B (o produto não embute preços); preço desatualizado distorce o custo registrado.
5. UI ponta a ponta com provedor real é guiada/manual (seção 6), pois o E2E automatizado da 02.6 usa gateway simulado.
6. Teto de custo do harness é conservador (pior caso); o custo real tende a ser bem menor.
7. Limites de Google Ads por tarefa/hora e do Keyword Planner continuam os da 02.5; o Keyword Planner pode exigir nível de token superior ao de teste.

---

## 11. Pré-voo final da Fase B (2026-11-23)

> **Fase B NÃO iniciada.** Nenhuma chamada real, nenhuma credencial configurada neste ambiente, nenhum deploy/merge/migration em banco real.

### 11.1 Itens do pré-voo

| # | Item | Situação |
|---|---|---|
| 1–2 | Jobs verdes da Actions `45237781296` e seção 9.1 | Ver 9.1 |
| 3 | `plan` e `preflight` sem credenciais | Executados: 17 operações listadas; **todas MISSING** (nenhuma credencial/preço presente); autorização **ausente**; teto agregado US$ 1,00, gasto registrado US$ 0,0000 |
| 4 | **Teto financeiro global** | **US$ 1,00 agregado provisório** para a Fase B inteira (harness + testes de agentes). Implementação em 11.2. Nada executado |
| 5 | Nenhuma credencial no Git, logs ou relatório | 11.3 |
| 6 | Modelos e preços oficiais | 11.4 |
| 7 | Proteção contra mutação no Google Ads | 11.5 |
| 8 | Checklist de credenciais por provedor | 11.6 |
| 9 | Não iniciar a Fase B | Cumprido |

### 11.2 Teto agregado único (US$ 1,00)

* `CostLedger` agora é **compartilhado por arquivo** (`backend/.homolog-ledger.json`, só números, no `.gitignore`; caminho alterável por `HOMOLOG_LEDGER_FILE`). O harness e a suíte `tests_real` leem e somam no **mesmo** arquivo.
* **Trava dura**: `--max-total-usd` maior que 1,00 é rebaixado a 1,00 (`HARD_MAX_TOTAL_USD`). Subir o teto exige alterar o código, com sua aprovação.
* **O teto é um orçamento ESTIMADO, não uma garantia de cobrança máxima no provedor** (ver 11.7). Defina também um limite de gasto no console de cada provedor.
* **Reserva atômica antes do envio** (corrigido em 11.8): todo ciclo ler-alterar-gravar do arquivo ocorre sob **trava exclusiva do SO** (`msvcrt`/`fcntl`, mais trava de thread). Uma chamada paga primeiro **reserva** o pior caso (`disponível = teto − gasto liquidado − reservas abertas`); sem saldo ou sem preço ⇒ não envia. Depois da resposta, a reserva é trocada pelo custo real (`settle`).
* **Harness**: `reserve` → envio → `settle(custo real)`. Se a chamada falha **depois** do envio (timeout, conexão caída, 5xx), o custo é desconhecido e a **reserva inteira é cobrada**, nunca devolvida.
* **Testes de agentes**: (a) cada teste reserva uma **fatia atômica** (≤ US$ 0,15) do teto; sem ao menos US$ 0,01 disponível ele é pulado; (b) a organização de teste recebe `AiBudget` diário e mensal **igual à fatia**, então o próprio AI Gateway recusa reservar além dela; (c) ao final, a fatia é liquidada pelo custo do `ai_usage_log` (custo calculado; ou a estimativa reservada para chamadas interrompidas) **mais** qualquer reserva do gateway ainda aberta; se alguma linha não tem custo nem estimativa, ou a medição falha, a **fatia inteira** é cobrada; (d) teto por tarefa ≤ US$ 0,10.
* Reservas de um processo que morreu **permanecem contadas** (conservador). Arquivo ilegível ⇒ **recusa tudo** (não zera). Reiniciar o contador exige apagar `backend/.homolog-ledger.json` conscientemente.
* **Estimativa**: com modelos baratos (11.4) a sonda do harness custa centavos (≈ 15 requisições de ≤ 256 tokens de saída). As tarefas de agentes são maiores; o teto agregado as interrompe (testes restantes ficam "não testados") se o gasto se aproximar de US$ 1,00. Prefira modelos de custo baixo.
* Testes: `test_aggregate_ceiling_is_hard_clamped_and_shared_through_the_ledger_file`, `test_reservation_is_atomic_across_processes` (8 processos disputando o teto: exatamente 3 reservas de US$ 0,30 vencem), `test_reservation_blocks_a_second_call_before_the_first_settles`, `test_unknown_cost_is_charged_in_full`, `test_unreadable_ledger_refuses_instead_of_resetting`, `test_interrupted_provider_call_is_charged_the_reserved_worst_case`, `test_default_aggregate_ceiling_is_one_dollar`.

### 11.3 Nenhum segredo no Git, nos logs ou no relatório

* Credenciais só em **variáveis de ambiente do seu shell**; nenhum arquivo versionado as lê (o compose de homologação não tem `env_file`). `.env*`, `*.local` e o ledger estão no `.gitignore`; nenhum `.env` é rastreado.
* **Relatório do harness**: só status, contagens, tokens, custo e detalhe curto, mascarado por `Redactor`.
* **Saída do pytest** da Fase B passa por `python -m app.homologation redact` (filtro de stdin) antes de ir ao terminal/disco e usa `--tb=line` (sem dump de variáveis locais).
* **Varredura de vazamento**: `scripts/homologation.sh scan` (e automaticamente ao fim de `phase-b`) procura nas evidências, docs e código qualquer **valor** de variável com `KEY/TOKEN/SECRET/REFRESH/PASSWORD` e padrões `sk-…`, `AIza…`, `ya29.…`, `1//…`, `Bearer …`; imprime só o **nome do arquivo**, nunca o valor; sai com erro se achar.
* Chaves dos provedores no stack isolado (BYOK) são criptografadas (Fernet) em banco descartável em memória, destruído ao final.
* Os resultados da Fase B só serão commitados depois do `scan` limpo e de sua revisão.

### 11.4 Modelos e preços oficiais (verificados em 2026-11-23)

**Modalidade usada em todas as linhas abaixo: Standard (síncrona, pay-as-you-go, contexto curto).** Batch, Flex, Fast/Priority e Ultrafast têm outras tarifas (tabela 11.4.1) e **não** se aplicam ao harness, que chama as APIs síncronas padrão. Fontes: páginas oficiais de preço relidas hoje, integralmente — [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI](https://developers.openai.com/api/docs/pricing), [Google](https://ai.google.dev/gemini-api/docs/pricing) (redireciona para aistudio.google.com/docs/pricing). **O sistema não embute preços**: você informa os valores ao configurar; eles mudam (há degraus de preço datados), então **reconfira no console no dia**. Preço de cache **não** é configurado no harness: tokens em cache seriam cobrados ao preço de entrada (conservador, sem subestimar).

| Provedor | Modelo (nome na página) | Entrada US$/Mtok | Saída US$/Mtok | Variáveis (micro-USD/Mtok: entrada / saída) |
|---|---|---|---|---|
| Anthropic | Claude Haiku 5.5 (prompts ≤ 100k) | 0,10 | 0,50 | `100000` / `500000` |
| Anthropic | Claude Sonnet 5.5 | 2 | 10 | `2000000` / `10000000` |
| Anthropic | Claude Opus 5.5 | 4 | 20 | `4000000` / `20000000` |
| OpenAI | gpt-6.1-terra (contexto curto ≤ 272k) | 1,00 | 5,00 | `1000000` / `5000000` |
| OpenAI | gpt-6-luna (contexto curto ≤ 272k) | 0,10 | 0,50 | `100000` / `500000` |
| OpenAI | gpt-6.2-sol | 2,00 | 10,00 | `2000000` / `10000000` |
| Gemini | gemini-3.5-flash-lite | 0,30 | 2,50 | `300000` / `2500000` |
| Gemini | gemini-4-hydrogen | 1,50 | 7,50 | `1500000` / `7500000` |
| Gemini | gemini-3.8-flash | 0,75 (até 31/12/2026; 1,50 depois) | 3,75 (7,50 depois) | `750000` / `3750000` |

#### 11.4.1 Outras modalidades (NÃO usar no harness)

| Modelo | Standard (entrada / saída) | Batch | Flex | Fast (ex-Priority) | Longo contexto (> 272k) Standard |
|---|---|---|---|---|---|
| OpenAI gpt-6-luna | 0,10 / 0,50 | 0,05 / 0,25 | 0,05 / 0,25 | 0,20 / 1,00 | 0,20 / 0,75 |
| OpenAI gpt-6.1-terra | 1,00 / 5,00 | 0,50 / 2,50 | 0,50 / 2,50 | 2,00 / 10,00 | 2,00 / 7,50 |
| OpenAI gpt-6.2-sol | 2,00 / 10,00 | 1,00 / 5,00 | não listado | 4,00 / 20,00 | 4,00 / 15,00 |
| Gemini 3.5 flash-lite | 0,30 / 2,50 | 0,15 / 1,25 | 0,15 / 1,25 | 0,54 / 4,50 | sem degrau |
| Gemini 4 hydrogen | 1,50 / 7,50 | 0,75 / 3,75 | 0,75 / 3,75 | 2,70 / 13,50 | sem degrau |
| Gemini 3.8 flash (até 31/12/2026) | 0,75 / 3,75 | 0,375 / 1,875 | 0,375 / 1,875 | 1,35 / 6,75 | sem degrau |
| Anthropic Haiku 5.5 | 0,10 / 0,50 | 0,05 / 0,25 | n/a | n/a | > 100k: 0,50 / 2,50 |
| Anthropic Sonnet 5.5 | 2 / 10 | 1 / 5 | n/a | n/a | sem acréscimo |
| Anthropic Opus 5.5 | 4 / 20 | 2 / 10 | n/a | fast mode: 8 / 40 | sem acréscimo |

**Conferência do gpt-6-luna:** a tarifa **Standard** na página oficial da OpenAI é **US$ 0,10 entrada / US$ 0,50 saída** por Mtok (cache de entrada US$ 0,01), idêntica à que já constava; **nenhuma divergência foi encontrada**. Os valores 0,05/0,25 (Batch/Flex) e 0,20/1,00 (Fast) são de outras modalidades e **não** devem ser configurados. Há ainda *Background* (30–40% abaixo do Standard), endpoints regionais/FedRAMP (+10%) e, para o gpt-6.2-sol, *Ultrafast* (3× o Standard até 28/02/2027, 6× depois). Usar tarifa menor que a cobrada **subestimaria** o custo.

Ressalvas verificadas:
* **Anthropic**: a página de preços usa nomes de exibição; a página *Models overview* lista os IDs `claude-haiku-5-5`, `claude-sonnet-5-5` e `claude-opus-5-5`. Confirme o ID exato no console ou em `GET /v1/models`; `ai.anthropic.verify` confere se `HOMOLOG_ANTHROPIC_MODEL` consta na lista da sua conta. Sonnet 5 a US$ 2/10 é o preço padrão (o aumento previsto para 01/09/2026 "não ocorrerá"). Haiku 5.5 acima de 100k tokens custa US$ 0,50/2,50 (irrelevante para as sondas).
* **OpenAI**: a página não declara suporte a tool-calling por modelo; confirme em *Models* antes. A tabela vem da leitura direta da página. A OpenAI não rotula uma modalidade como "padrão"; Standard é a primeira listada e a síncrona comum (inferência de layout).
* **Gemini**: nesta releitura a página foi lida inteira (em dois blocos); as linhas dos três modelos acima são literais (Standard, pago). O preço de saída **inclui os tokens de raciocínio** ("thinking"), e o harness já limita a saída a 256 tokens. A página não rotula um nível como padrão; Standard é o primeiro. **Degraus datados**: Gemini 4 Argon/Carbon dobram em 01/12/2026; 3.6/3.8 Flash dobram em 01/01/2027, o que importa se a Fase B ocorrer perto dessas datas.
* Preço verificado ≠ modelo suportado pelo produto: o modelo precisa suportar tool-calling e JSON (a Fase B confirma).

### 11.5 Proteção contra mutação no Google Ads (quatro camadas)

1. **Estática** (CI): teste varre `app/homologation/*` e o leitor por `mutate`, criar/pausar/ativar campanha e negativos.
2. **Allowlist de operações**: o harness só executa 6 operações `gads.*` de leitura; id fora da lista é rejeitado.
3. **Execução (nova, `homologation/readonly.py`)**: o cliente do SDK é embrulhado e só alcança `GoogleAdsService.search/search_stream` (e os helpers de caminho), `CustomerService.list_accessible_customers` e `KeywordPlanIdeaService.generate_keyword_ideas`. Qualquer outro serviço (`CampaignService`, `CampaignBudgetService`…), método (`mutate`, `create_customer_client`…) ou GAQL que não seja um único `SELECT` (sem `;`) levanta `MutationBlocked` **antes** de montar a requisição. É instalado nas duas fábricas de cliente da aplicação (`google_ads_reader.CLIENT_FACTORY` e `google_ads_service._build_client`) durante o harness **e** durante todos os testes `tests_real`, de modo que nem os endpoints de escrita da API alcançariam o Google. Testes: `test_readonly_guard_blocks_mutations_before_any_request`, `test_readonly_install_wraps_both_application_factories`.
4. **No Google**: use refresh token de usuário com acesso **somente leitura** à conta (e à MCC), a defesa que não depende do nosso código.

Limite honesto: a camada 3 foi provada contra um cliente falso e na fiação das fábricas; o comportamento do SDK real só se confirma na Fase B (somente leitura).

### 11.6 Checklist de configuração (nenhum valor deve ser colado no chat)

Configure **no seu terminal**, na sessão que rodará a Fase B (ex.: `export NOME=...` ou um gerenciador de segredos); não grave em arquivo dentro do repositório. Depois rode `scripts/homologation.sh preflight`: ele mostra só "set/MISSING", nunca valores.

**Para cada provedor de IA que quiser testar** (`ANTHROPIC`, `GEMINI`, `OPENAI`):
- [ ] Chave **de teste**, de projeto/organização dedicados, com **limite de gasto** definido no console do provedor
- [ ] Faturamento/crédito ativo no provedor
- [ ] `HOMOLOG_<P>_API_KEY`
- [ ] `HOMOLOG_<P>_MODEL` (ID exato; conferir em *list models* e que suporta tool-calling + JSON)
- [ ] `HOMOLOG_<P>_INPUT_PRICE_MICROS_PER_MTOK` e `HOMOLOG_<P>_OUTPUT_PRICE_MICROS_PER_MTOK` (tabela 11.4, reconferida no dia)
- [ ] Pelo menos **2 provedores** configurados, se quiser o teste real de fallback

**Google Ads (somente leitura):**
- [ ] Projeto Google Cloud com *Google Ads API* habilitada; cliente OAuth tipo Web → `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`
- [ ] `GOOGLE_ADS_DEVELOPER_TOKEN` (anotar o **nível**: *Test* só lê contas de teste; o Keyword Planner pode exigir nível maior)
- [ ] `HOMOLOG_GADS_REFRESH_TOKEN` com escopo `adwords`, de usuário com acesso **somente leitura**
- [ ] `HOMOLOG_GADS_CUSTOMER_ID` (preferir conta de teste ou sem campanhas ativas); `HOMOLOG_GADS_LOGIN_CUSTOMER_ID` se houver MCC
- [ ] Redirect URI do cliente OAuth idêntico ao `GOOGLE_ADS_OAUTH_REDIRECT_URI`, se for refazer o fluxo OAuth

**Opcional:** `HOMOLOG_PRODUCT_URL` (página pública para o `product_analyst`).

**Para disparar (só após sua autorização explícita):**
- [ ] `export HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B` — **não defina antes**; sem a frase nada é enviado
- [ ] `scripts/homologation.sh phase-b`
- [ ] `scripts/homologation.sh scan` limpo antes de commitar `docs/evidence/etapa-02-07/`
- [ ] Revogar/rotacionar as chaves de teste ao terminar

### 11.7 O que o teto de US$ 1,00 é e o que não é

O teto é um **limite de orçamento ESTIMADO da homologação**, calculado por nós (pior caso antes do envio, custo calculado a partir do `usage` depois). **Não é garantia de cobrança máxima no provedor.** O valor faturado pode divergir porque:

* **Chamadas interrompidas** (timeout, conexão caída, cancelamento): o provedor pode ter processado e cobrado sem devolver `usage`. Tratamento: o custo é tratado como **desconhecido** e a **reserva inteira (pior caso) é cobrada** do orçamento, nunca devolvida; nos testes de agentes vale a estimativa reservada do `ai_usage_log`, mais reservas do gateway ainda abertas, e na dúvida a fatia inteira.
* **Tokens de raciocínio/"thinking"**, cache, ferramentas embutidas, impostos, arredondamentos e retentativas do lado do provedor, que a nossa estimativa (3 caracteres por token) pode subestimar.
* **Preço informado por você** diferente do vigente (ver 11.4, modalidade Standard).
* **Reservas deixadas por processo morto** continuam contadas (podem superestimar).
* Cobranças **fora do nosso controle** (a leitura da Google Ads API é gratuita, mas sujeita a cotas; o provedor de IA fatura pelo console).

Por isso: (1) use chaves de **projeto dedicado** com **limite de gasto/alerta no console do provedor**; (2) compare o custo registrado com a fatura do console depois da Fase B e registre a diferença; (3) a trava dura de US$ 1,00 do código limita o que **nós** enviamos, não o que o provedor fatura.

### 11.8 Correção final do pré-voo (base `ecbfc84`)

| # | Pedido | Resultado |
|---|---|---|
| 1 | Preço do gpt-6-luna (Standard) | Verificado na página oficial: **0,10 / 0,50**, igual ao já documentado; **sem divergência**. Ver 11.4.1 |
| 2 | Reconferir demais modelos e modalidades | Anthropic, OpenAI e Gemini relidos; modalidades separadas em 11.4.1; Gemini lido por inteiro; IDs Anthropic confirmados |
| 3 | Bloqueio atômico entre processos | **Havia uma falha real**: o contador lia o arquivo só ao ser construído e gravava o total em memória; dois processos podiam gastar o mesmo saldo e um sobrescrevia o outro, e o saldo só era checado, sem reserva. Corrigido: trava de arquivo + reserva antes do envio (11.2) |
| 4 | Custo desconhecido / cobranças externas | Reserva inteira cobrada em chamada interrompida; fatia inteira cobrada quando não há medição (11.2, 11.7) |
| 5 | Teto = orçamento estimado | Documentado (11.7); `preflight` e o relatório do harness trazem o aviso (`cost_basis`) |
| 6 | Testes e CI | Ver a entrega (commit e run da Actions) |
| 7 | Relatório | Esta seção |

Nenhuma chamada real, credencial, deploy, merge ou migration foi feita. A Fase B continua **não iniciada** e aguarda sua autorização explícita.
