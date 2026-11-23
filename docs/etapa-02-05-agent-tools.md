# Etapa 02.5 — Ferramentas reais dos agentes

Branch: `feat/etapa-02-05-agent-tools` (a partir de `feat/etapa-02-04-agent-worker`, `6cda39ee`).

> ## Status de homologação — leia antes de tudo
> **Nenhum dado do Google Ads foi homologado.** Toda a validação usa **dublês** (`tests/unit/gads_support.py`) com a *forma* dos objetos do SDK
> (`row.campaign.id`, `row.metrics.clicks`, enums com `.name`…) para que o código real de leitura, validação e proteção rode. **Isso não prova que a API
> real responde assim.** Não foi usada nenhuma credencial real, nenhuma chamada ao Google, nenhuma chamada paga a provedor de IA, nenhuma migração
> (esta etapa **não tem migração**) e nenhum deploy. O frontend não foi alterado. Os agentes continuam sem permissão de publicar ou gastar.
> Para chamar o conjunto de ferramentas de "homologado" falta uma execução, com autorização e uma conta de teste, em cada ferramenta (seção 9).

## 1. O que foi entregue

| # | Requisito | Entrega |
|---|---|---|
| 1–2 | Leitura real do Google Ads (conta, campanhas, grupos, anúncios, métricas) | 6 ferramentas de **leitura**: `google_ads.get_account_info`, `get_account_structure`, `get_campaign_performance`, `get_ad_group_performance`, `get_ad_performance`, `get_search_terms_report` |
| 3 | Keyword Planner (ideias, volume, CPC estimado) | `google_ads.keyword_ideas`: devolve o que o Google fornecer (volume médio mensal, concorrência, faixa de lance do topo da página); o que não vier é `None` |
| 4 | Análise real de URLs de produto/páginas de vendas | `web.analyze_page`: extração determinística do que a página **declara** (preço, oferta, garantia, frete, CTAs, dados estruturados JSON-LD, políticas, prova social, urgência) com o trecho de origem, **e** do que **não** encontrou (`not_found`) |
| 5 | Ferramentas conectadas aos agentes | `product_analyst` (+`web.analyze_page`), `market_researcher` (+planner, termos de pesquisa, estrutura), `search_ads_strategist` (+estrutura, desempenho, planner), `performance_analyst` (+todas as de conta). `ad_copywriter` e `policy_reviewer` não mudam |
| 6–7 | Diferenciar real / estimativa / hipótese; não inventar métricas | Seção 3 |
| 8 | Permissões, escopos, RBAC, RLS, limites de chamadas, isolamento | Seção 4 |
| 9 | Agentes sem publicar nem gastar | Seção 5 |
| 10 | Chamadas de IA interrompidas: contabilização e limites | Seção 6 |
| 11 | Preservar o worker dedicado e a Central de IA | Nada do worker/queue foi alterado; o gateway só ganhou o tratamento de cancelamento (seção 6) |

Versões dos agentes: `product_analyst`, `market_researcher`, `search_ads_strategist`, `performance_analyst` passam a **1.1.0** (as instruções mudaram; os hashes
fixados no teste foram atualizados). `GET /agents/tools` agora lista `external_system` e `requires` (ex.: "a connected Google Ads account of the organization")
e só os dois contratos que mudam conta (`draft_campaign`, `apply_change_proposal`) seguem como `contract_only`.

## 2. Arquitetura

```
agente (worker) ──tool call──► runner: allow-list do agente → RBAC do usuário (ads:read) → argumentos validados (extra=forbid)
                                   │
                                   ▼ google_tools.py
        limites (agent_steps) ─► conexão DA ORGANIZAÇÃO (RLS + organization_id + vínculo ativo) ─► token válido (refresh se preciso)
                                   │
                                   ▼ google_ads_reader.py  (thread; só search_stream / generate_keyword_ideas)
                                   │                                 ▲ CLIENT_FACTORY (o teste troca; produção = SDK com o token da org)
                                   ▼
        resultado {available, data…, provenance{data_kind, source, retrieved_at}}  +  ledger de evidências (evidence.py)
                                   │
        saída do agente ─► validadores (data_basis / palavras-chave) conferem cada número citado contra o ledger ─► reparo ou invalid_output
```

* `app/services/google_ads_reader.py`: leitura GAQL e Keyword Planner. **Sem** `mutate_*`, **sem** import dos helpers de mutação de `google_ads_service` (teste estático por AST).
  Consultas montadas só com constantes validadas (datas ISO, ids numéricos); texto do usuário nunca entra no GAQL.
* `app/agents/google_tools.py`: as 7 ferramentas Google + `web.analyze_page`, cotas, resolução da conexão, erros.
* `app/agents/page_analysis.py`: extração da página. `app/agents/evidence.py`: ledger e regras de honestidade.
* `Tool` ganhou `external` e `requires`; `ToolContext` ganhou `evidence` e `upstream`. O executor, o supervisor, o worker e a fila **não mudaram**
  (o supervisor só passa o ledger aos validadores).

## 3. Real × estimativa × hipótese

| Rótulo | Significado | De onde pode vir | Como é verificado |
|---|---|---|---|
| `real` | fato devolvido por uma ferramenta **desta execução** ou dado enviado pelo solicitante | `google_ads_api` (métricas/estrutura da conta), `product_page` (o que a página declara), `supplied_context` | cada métrica citada (`entity`, `name`, `value`) tem de **igualar** um valor que a ferramenta retornou (ou um número presente nos dados enviados / resultados de agentes anteriores) |
| `estimate` | estimativa do próprio Google | **somente** `google_keyword_planner` | idem; precisa citar ao menos uma métrica; `real` com origem Planner e `estimate` com origem conta são rejeitados |
| `hypothesis` | ideia/inferência do modelo | qualquer | **não pode citar nenhum número**; conhecimento do modelo (`model_knowledge`) só pode ser hipótese |

* **Onde o rótulo vive:** `data_basis` (todas as saídas dos 4 agentes) e, em `market_researcher`, em **cada palavra-chave** (`data_kind`, `source`, `metrics`; o padrão é `hypothesis`/`model_knowledge`/sem métricas).
* **Validação no servidor** (`registry.validators_for` → `evidence.basis_problems`): violação ⇒ **um** turno de reparo com a lista exata dos problemas; persistindo, a tarefa falha com `invalid_output` e **nada inventado é gravado**. Fonte lida? Citar `google_ads_api` sem que nenhuma ferramenta Google tenha lido a conta nesta execução é problema, mesmo sem número.
* **Quando a API não devolve dado:** a ferramenta responde `{"available": false, "reason": …, "notice": "No data was returned. Do not estimate or invent…"}` **sem nenhum campo de dados**; se o Google responde sem linhas, `no_data: true` e `rows: []`. Razões: `not_configured`, `not_connected`, `account_not_linked`, `rate_limited`, `access_denied`, `google_quota`, `connection_error`, `timeout`, `invalid_range`, `invalid_argument`, `api_error`. Uma leitura que falhou **nunca** entra no ledger.
* **Nunca zero por ausência:** razões (CTR, CPC médio, custo por conversão) só são calculadas com denominador não nulo, senão `None`; no Keyword Planner volume/concorrência ausentes são `None` e lance `0` (que o SDK usa para "não informado") vira `None`.
* **Proveniência em todo resultado:** `provenance.data_kind` (`real` métricas de conta, `estimate` Planner), `source`, `retrieved_at` e nota (ex.: "dias recentes ainda podem mudar", "estimativas do Google, não medições").

## 4. Segurança

* **Leitura apenas.** Todas as ferramentas têm `side_effect = "none"`; nenhum argumento aceita `confirm`, aprovação, orçamento ou status (teste); o reader só chama `search_stream` e `generate_keyword_ideas`.
* **Isolamento por organização.** A conexão é sempre a da organização da **tarefa** (`ctx.org_id`), filtrada explicitamente além do RLS (`google_ads_connections`, `ad_account_links`). `customer_id` é só um seletor opcional e precisa ser um **vínculo ativo da mesma organização**, senão `account_not_linked` (mesma resposta de "não existe", **sem** carregar o token de outra organização nem chamar o Google). Conta apenas candidata (OAuth sem vínculo ativo) e vínculo revogado não leem. O MCC (`login_customer_id`) é o da própria organização. Testes: dois tenants concorrentes, cada um só com seus dados, token e MCC.
* **RBAC.** `ads:read` do **usuário solicitante**, reavaliado em cada chamada (`tools.authorize`); papel rebaixado antes da execução ⇒ `authorization` e nenhuma chamada ao Google. Executar o agente continua exigindo `agents:run`.
* **Escopos OAuth.** O fluxo da etapa 01.3 só pede `https://www.googleapis.com/auth/adwords`. **O Google Ads não oferece escopo somente-leitura**: a leitura é garantida por código e por teste, não pelo escopo (ver pendências).
* **Segredos.** Refresh token só é descriptografado dentro da ferramenta, usado para construir o cliente e nunca devolvido; `redact_secrets` continua valendo; teste varre passos, execuções, resultados e auditoria atrás de tokens/developer token/client secret. Erros do Google viram um motivo curto (sem texto do provedor, sem `request_id`).
* **Limites de chamadas** (suaves, contados a partir de `agent_steps`, portanto valem entre tentativas e entre workers): por tarefa e por organização/hora, com limites **próprios e menores para o Keyword Planner**. Atingido ⇒ `rate_limited` visível ao agente e **nenhuma chamada ao Google**; `0` desliga a família.
* **Conteúdo hostil.** Termos de pesquisa, texto de página e nomes de conta são dados de terceiros: entram no prompt dentro de `<untrusted_tool_output>`, injeção suspeita é marcada (`agent.injection.suspected`) e o bloco `DATA HONESTY` das instruções manda ignorar instruções ali contidas. A análise de página nunca interpreta o texto, só o reporta.
* **SSRF.** `web.analyze_page` usa o mesmo buscador protegido da etapa 02.3 (somente endereços públicos).

## 5. Sem publicar, sem gastar

Nenhum agente tem ferramenta de escrita; `draft_campaign` e `apply_change_proposal` seguem `contract_only` (o registro recusa executar). Testes: allow-lists só com as 7 ferramentas de leitura Google; análise AST do reader/ferramentas sem nenhum identificador de mutação; toda consulta GAQL registrada começa com `SELECT`; só `product_analyst`/`market_researcher`/`search_ads_strategist`/`performance_analyst` leem a conta ou a página.

## 6. Chamadas de IA interrompidas (item 10)

**Problema.** Uma chamada ao modelo cancelada (desligamento do worker) ou abortada por tempo (`asyncio.wait_for` do runner) levantava `CancelledError`, que o gateway não tratava: a **reserva de orçamento ficava pendurada** até o TTL (`AI_RESERVATION_TTL_SECONDS`, 15 min), **nenhuma linha de uso** era gravada e o **custo não entrava no total da tarefa** — o limite por tarefa podia ser ultrapassado depois de uma retomada.

**Correção.**
* `AIGateway.chat`: ao ser interrompida em voo, a reserva **nunca é liberada** (o provedor pode ter processado e cobrado): é **liquidada pela estimativa** (conservador), o uso é registrado (`status=error`, `error_kind=interrupted`, `cost = estimativa`) e a estimativa fica em `gateway.interrupted_cost_micros`; a interrupção continua propagando. Sem preço conhecido nada é inventado (`None`), mas o registro é gravado. O marcador é zerado a cada chamada.
* `AgentRunner._chat`: em `TimeoutError` e em `CancelledError` o custo cobrado é gravado como passo `llm` (erro `timeout`/`interrupted`) e **somado** ao custo da execução e da tarefa, de modo que o **teto de custo da tarefa vale também depois de devolvida ao worker e retomada** (e a pré-checagem da 02.4 impede nova chamada paga já acima do teto).

## 7. Configuração

Variáveis novas (todas com padrão; ver `.env.example`): `AGENT_GOOGLE_ADS_MAX_CALLS_PER_TASK` (15), `AGENT_GOOGLE_ADS_MAX_CALLS_PER_ORG_HOUR` (120), `AGENT_KEYWORD_PLANNER_MAX_CALLS_PER_TASK` (4), `AGENT_KEYWORD_PLANNER_MAX_CALLS_PER_ORG_HOUR` (20), `AGENT_GOOGLE_ADS_TIMEOUT_SECONDS` (25). As ferramentas exigem `GOOGLE_ADS_CLIENT_ID/SECRET/DEVELOPER_TOKEN` no servidor **e** uma conta conectada e vinculada pela organização (fluxo OAuth existente); sem isso respondem `not_configured`/`not_connected`. O worker precisa das mesmas variáveis do Google que a API.

## 8. Testes

Banco **descartável**; gateway scriptado/`FakeGoogle` — nenhuma rede.

| Arquivo | Testes | O que prova |
|---|---|---|
| `test_agents_google_tools.py` | 42 | **Contratos** de cada ferramenta contra o SDK simulado (métricas, moeda, proveniência, razões `None`, `no_data`, intervalo padrão/validação, consulta só com constantes, Planner: estimativa/ausente=`None`/lance 0=`None`/ordem/requisição com idioma, geo, sementes e rede de busca). **Erros**: sem conexão, sem credenciais, candidata/revogada, 7 falhas do provedor sem vazar texto, grant revogado desativa a conexão. **Isolamento**: token+MCC próprios, `customer_id` de outra org recusado sem tocar o Google, RLS. **RBAC**, segredos. **Limites** por tarefa/org-hora/Planner/zero. **Somente leitura** (AST, `SELECT`, contratos de escrita). **Regras de evidência** |
| `test_agents_data_honesty.py` | 25 | **Ponta a ponta no worker**: métrica real citada e gravada; **métrica inventada recusada** (1 reparo, depois `invalid_output`, nada gravado); alegar dado sem ler; hipótese com número; conhecimento do modelo como real; sem conta ⇒ indisponível e alegação recusada; números fornecidos; limite visível ao agente; termos hostis marcados; Planner = estimativa verificada, volume inventado/palavra ausente recusados, rótulos trocados recusados; página (preço real verificado, preço inexistente recusado); estratégia com estrutura+desempenho; números de agente anterior; **duas organizações concorrentes**; `customer_id` alheio; **nenhum segredo** em passos/execuções/resultados/auditoria; papel rebaixado |
| `test_agents_page_analysis.py` | 20 | Extração (preços de dados estruturados e texto, nunca de `<script>`; formatos BR/US; garantia, frete, CTAs, pagamento, políticas, contato; `not_found`; aviso de página renderizada por JS; riscos de política; HTML quebrado; texto hostil é só dado; saída limitada), ferramenta (fetch protegido, falhas sem fatos, SSRF) |
| `test_ai_interrupted_calls.py` | 8 | Gateway real + orçamentos: cancelamento e timeout **cobram a estimativa** e não deixam reserva; o limite vale para a chamada seguinte; sem preço não inventa; marcador zerado; **no worker**: timeout e desligamento mantêm o custo no passo, na execução e na tarefa, e a tarefa retomada respeita o teto |
| Testes anteriores ajustados | — | hashes/versões dos agentes, lista de ferramentas disponíveis × contratos |

## 9. Pendências e riscos (honestos)

1. **Nada homologado com o Google Ads real.** Falta, com autorização e uma conta de teste: executar cada ferramenta, conferir os nomes de campos GAQL e o formato real do SDK (os dublês foram escritos a partir da documentação, não de respostas reais), o comportamento de `search_term_view`, de `keyword_plan_idea` por nível de *developer token* e a ausência de campos (volume pode vir em faixas arredondadas).
2. **Escopo OAuth não é somente-leitura** (o Google não oferece). Uma regressão no código que introduzisse uma mutação teria permissão para executá-la; a proteção é a de código/teste (AST, `SELECT`, allow-lists) e a revisão humana — considerar um *developer token*/conta de leitura dedicada.
3. **Limites de chamadas são suaves**: contam `agent_steps` já gravados, então duas tarefas simultâneas podem ultrapassar o limite por organização por poucas chamadas. Não protegem a cota diária de operações do Google, apenas limitam o consumo dos agentes.
4. **Timeouts do provedor de IA vistos pelo adaptador** (`ai_timeout` levantado pelo próprio adaptador, não por cancelamento) continuam **liberando** a reserva e acionando o *fallback*, como antes; só o cancelamento/abort **do chamador** passou a cobrar. Se o provedor cobra requisições que estouram o timeout do adaptador, esse custo ainda não é contabilizado.
5. **Custo interrompido sem preço** (`modelo sem preço`) não entra no teto da tarefa (desconhecido); o orçamento da organização já falha fechado nesse caso.
6. A verificação cobre **números citados** em `data_basis`/palavras-chave; um número escrito só em texto livre (resumo, achados) não é verificável automaticamente — as instruções proíbem, mas a garantia dura é a dos campos estruturados. Frases livres continuam exigindo revisão humana.
7. Análise de página: lê o HTML como servido (sem JavaScript, sem cabeçalhos/status HTTP) e reporta o que a página **diz**, sem verificar se é **verdade**; regras de política são heurísticas estáticas, não decisão oficial do Google Ads.
8. `get_valid_access_token` pode gravar o novo *access token* (commit) dentro da sessão do executor e desativa a conexão se o *refresh* falhar (comportamento já existente da API, agora também acionado por agentes).
9. Sem painel de interface para estas ferramentas (frontend congelado): `GET /agents/tools` já expõe `external_system`/`requires` para a etapa de interface.
10. O reader não pagina além dos limites por consulta (25 campanhas na estrutura; 50–100 linhas de desempenho; 100–200 termos) e indica `truncated`.

## 10. Evidências

| Verificação | Resultado |
|---|---|
| Backend, suíte completa (PostgreSQL descartável, migrações do zero) | **953 passaram, 1 ignorado** (SIGTERM em POSIX, no Windows). Uma falha apareceu na execução completa (`test_health_endpoint…` da 02.4, corrida de temporização do próprio teste: `AGENT_WORKER_STALE_SECONDS=0.0001` podia ainda não ter vencido); o teste foi tornado determinístico (`-1`) e passou 3 de 3 isolado. Cobertura total 77,07% (`google_tools` 98%, `evidence` 97%, `page_analysis` 96%, `google_ads_reader` 92%, `gateway` 96%, `runner` 96%) |
| Novos testes | 95: `test_agents_google_tools` 42, `test_agents_data_honesty` 25, `test_agents_page_analysis` 20, `test_ai_interrupted_calls` 8 |
| **Modo de banco seguro** (login sem privilégios, `APP_ENV=production`) | os 95 novos passaram |
| Migrações | **nenhuma** nesta etapa (head continua `f2a3b4c5d6e7`) |
| Frontend (não alterado, `git status frontend` limpo): lint / unit / i18n / build | 0 erros (26 avisos, os mesmos) / 191 / 89 / build ok |
| Segurança | `bandit -r app/ -ll -ii`: sem achados (exit 0); `pip-audit -r requirements.txt`: sem vulnerabilidades (dependências inalteradas) |
| CI | passo do modo seguro ampliado com os 4 arquivos novos |

Como rodar: `bash scripts/test-backend.sh tests/unit/test_agents_google_tools.py tests/unit/test_agents_data_honesty.py tests/unit/test_agents_page_analysis.py tests/unit/test_ai_interrupted_calls.py`.

**Lembrete: tudo acima é com dublês. Dados do Google Ads reais não foram homologados.**
