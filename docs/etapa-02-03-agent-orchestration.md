# Etapa 02.3 — Núcleo dos agentes de IA

Branch: `feat/etapa-02-03-agent-orchestration` (a partir de `feat/etapa-02-02-ai-control-center`, `109728e`).

> **Status de homologação:** o sistema de agentes é real (supervisor, especialistas, tool-calling, persistência, recuperação)
> e usa o AI Gateway da organização. **Mas toda a validação desta etapa usa provedores simulados** (`provider_mock` nos formatos de
> fio de Anthropic, Gemini e OpenAI, e um gateway *scriptado* nos testes de lógica). **Nenhum agente foi homologado contra uma API
> real**, nenhuma chamada paga foi feita, nenhuma mutação foi feita no Google Ads e a migração `e1f2a3b4c5d6` **não foi
> executada** em desenvolvimento ou produção (só no PostgreSQL descartável dos testes).
> **Nesta etapa os agentes só leem e planejam: nenhum pode publicar campanha ou gerar gasto.**

## 1. Agentes implementados

Todos rodam sob uma *operação* do AI Gateway (`agent.<chave>`): **provedor, modelo, fallbacks, credenciais e orçamento vêm do
roteamento da própria organização** (nada de nome de modelo no código; um teste estático garante). Cada execução grava a
versão e o hash das instruções que a produziram.

| Chave | Função | Ferramentas permitidas | Saída (schema Pydantic) | Prontidão hoje |
|---|---|---|---|---|
| `product_analyst` | Análise de produtos e páginas de vendas | `catalog.*`, `web.fetch_page`, `task.get_context`, `plan.note` | `ProductAnalysis` | **Operacional** (modelo real + leitura de catálogo e página pública) |
| `market_researcher` | Pesquisa de mercado e palavras-chave | `catalog.*`, `task.get_context`, `plan.note` | `MarketResearch` (palavras-chave como **hipóteses**; sem campo de volume/CPC) | **Limitado**: sem dado medido até existir a leitura do Google Ads |
| `search_ads_strategist` | Estratégia Google Search Ads | `catalog.*`, `task.get_context`, `plan.note` | `SearchAdsStrategy` | **Operacional para planejar**; não cria nada na conta |
| `ad_copywriter` | Redação e anúncios responsivos (RSA) | `catalog.get_*`, `task.get_context`, `ads.validate_rsa`, `policy.check_text`, `plan.note` | `AdCopy` (30/90 caracteres, 3–15 títulos, 2–4 descrições; revalidado no servidor) | **Operacional** (validação determinística dos limites) |
| `policy_reviewer` | Políticas e conformidade | `task.get_context`, `policy.check_text`, `ads.validate_rsa`, `plan.note` | `PolicyReview` (veredito não pode aprovar com *blocker*) | **Consultivo**: regras estáticas + julgamento do modelo; **não** é decisão oficial do Google Ads |
| `performance_analyst` | Otimização e análise de desempenho | `task.get_context`, `catalog.get_product`, `plan.note` | `PerformanceAnalysis` (limitações de dados obrigatórias) | **Limitado**: analisa dados **fornecidos** pelo solicitante; não lê a conta |

O **Supervisor** (`agent.supervisor`) não tem ferramentas e não é alvo de delegação.
`GET /agents` devolve a prontidão e as limitações de cada agente: nada é apresentado como operacional se não for.

## 2. Arquitetura

```
POST /agents/tasks ──► create_task (idempotência, teto de tarefas ativas, advisory lock por organização)
        │                    └─ grava agent_tasks.status = pending  (+ audit)
        └─ BackgroundTasks ──► execute_task
                                 ├─ CLAIM atômico: UPDATE … WHERE status='pending' (lease_owner, lease_expires_at, attempts+1)
                                 ├─ fecha execuções órfãs (interrupted), recalcula o prazo, confere o papel do usuário
                                 ├─ heartbeat da lease (sessão própria)
                                 └─ run_single | run_supervised
                                       Supervisor: plano (validado) → especialistas em ordem → consolidação
                                       Runner: loop modelo ⇄ ferramentas  ──► AI Gateway (credencial, rota, fallback, orçamento da org)
```

* **Supervisor** (`supervisor.py`): planeja com **uma** chamada sem ferramentas; o plano é validado (especialistas conhecidos e habilitados, ids únicos, dependências só em passos anteriores, no máximo 6 passos, nunca delega a si mesmo; um especialista não delega: profundidade 1). Plano inválido recebe **um** reparo; depois `invalid_plan`. Cada passo recebe as saídas das dependências como **dados não confiáveis**. A falha de um especialista falha a tarefa e **preserva** o que já foi produzido (`result.partial`).
* **Runner** (`runner.py`): `max_steps`, `max_tool_calls`, prazo, teto de custo da tarefa, **detecção de loop** (mesma ferramenta+argumentos mais de 2 vezes é recusada; segunda recusa encerra com `loop_detected`), cancelamento e lease verificados antes de cada chamada ao modelo, resposta final validada contra o schema (um turno de reparo, depois `invalid_output`).
* **Estados** (`agent_tasks.status`): `pending → running → completed | failed | cancelled`.
* **Lease com *fencing***: toda escrita do executor tem `WHERE lease_owner = :owner AND status = 'running'`. Se uma recuperação re-enfileirou a tarefa, o executor antigo falha na próxima escrita (`LostLease`) e para: **dois executores nunca avançam a mesma tarefa**.
* **Recuperação de tarefas interrompidas** (`recover_interrupted_tasks`): lease vencida ⇒ volta a `pending` (até `max_attempts`, depois `failed/interrupted`); `cancel_requested` ⇒ `cancelled`. Roda no startup (em segundo plano, nunca bloqueia a API), por `POST /agents/recover` (somente a organização do chamador) e pode ser agendada. **Retomada segura**: o plano salvo é reaproveitado, execuções `completed` **não são refeitas**, a que estava em andamento é fechada como `interrupted` e recomeçada (as ferramentas são só leitura, então repetir é seguro; o custo das chamadas anteriores já foi contabilizado pelo gateway).
* **Cancelamento**: tarefa `pending` é cancelada na hora; `running` recebe a marca e para antes da próxima chamada ao modelo (uma chamada já em voo termina).
* **Tempo**: limite por agente (`timeout_seconds`) dentro do limite da tarefa (`AGENT_TASK_TIMEOUT_SECONDS`).

### Persistência (PostgreSQL, RLS `tenant_isolation` em todas)

| Tabela | Conteúdo |
|---|---|
| `agent_tasks` | pedido, estado, plano, resultado, erro, tentativas, lease, totais (tokens, custo), prazo, `idempotency_key` único por organização |
| `agent_runs` | uma execução de especialista (versão e hash das instruções, saída, custo) com FK composta (`task_id`, `organization_id`) |
| `agent_steps` | histórico **somente de inserção** (UPDATE/DELETE/TRUNCATE revogados do papel da aplicação): turno do modelo, chamada de ferramenta, reparo; entradas/saídas **redigidas e truncadas** antes de gravar; nunca guarda prompt |
| `agent_settings` | habilitar/desabilitar um agente e limites reduzidos por organização (dentro de tetos fixos) |

`agent_tasks` tem **uma** política extra, `platform_recovery`, satisfeita só pelo contexto assinado de administrador de plataforma: serve exclusivamente ao job de recuperação (que só enxerga/atualiza colunas de estado) e **não** dá acesso às demais tabelas.

## 3. Segurança

* **Ferramentas somente de leitura/planejamento** (`side_effect = "none"`, verificado por `assert` na importação e por testes). Nenhum agente lista um `google_ads.*`; as ferramentas fora da lista do agente são recusadas no servidor mesmo que o modelo as peça (inclusive depois de uma injeção bem-sucedida: há teste).
* **Argumentos** validados com Pydantic `extra="forbid"`: o modelo não escolhe a organização; ela vem da tarefa, e toda consulta ainda filtra por `organization_id` além do RLS.
* **RBAC** do usuário solicitante reavaliado a cada chamada de ferramenta e antes de executar (papel rebaixado ⇒ `authorization`).
* **Prompt injection**: todo conteúdo de ferramenta, contexto da tarefa e saídas de outros agentes entram no prompt dentro de `<untrusted_tool_output …>` (tag de fechamento forjada é neutralizada), com instruções de sistema que o declaram **dado**; texto com cara de instrução é marcado (`injection_suspected`), auditado (`agent.injection.suspected`) e avisado ao modelo. A defesa real é estrutural (allow-list, somente leitura, sem acesso a credenciais), não uma lista de palavras.
* **Vazamento de segredos**: `redact_secrets` remove padrões de chave/token/JWT/DSN **e os valores dos segredos do próprio servidor**; é aplicado ao que o modelo vê, ao que é gravado, ao objetivo/contexto do pedido e à saída do modelo. Erros de ferramenta são genéricos. Nenhuma ferramenta toca `ai_credentials`.
* **Auditoria** (`audit_logs`, sem conteúdo): `agent.task.created|started|completed|failed|cancelled|cancel_requested|recovered`, `agent.tool.denied`, `agent.injection.suspected`, `agent.settings.updated`, `permission.denied`.

## 4. APIs (`/api/v1/agents`)

Permissões novas: `agents:read` (analista+), `agents:run` (gerente+), `agents:manage` (admin+).

| Método e caminho | Permissão | Descrição |
|---|---|---|
| `GET /agents` | read | agentes: versão, hash, ferramentas, limites (padrão e efetivo), rota do gateway, prontidão, limitações, schema de saída |
| `GET /agents/tools` | read | ferramentas **disponíveis** e contratos **planejados** (`contract_only`) |
| `PUT /agents/{key}/settings` | manage | habilitar/desabilitar, `max_steps` (1–20), `timeout_seconds` (10–600) |
| `POST /agents/tasks` | run | inicia (`objective`, `agent_key?`, `context?`, `idempotency_key?`, `max_cost_usd?`); sem `agent_key` = supervisionada |
| `GET /agents/tasks?status=&limit=` | read | lista |
| `GET /agents/tasks/{id}` | read | **progresso**: estado, plano, execuções, uso, erro, `progress{completed_steps,total_steps,final}` |
| `GET /agents/tasks/{id}/steps?after=&limit=` | read | histórico paginado |
| `GET /agents/tasks/{id}/result` | read | resultado (409 enquanto não terminou) |
| `POST /agents/tasks/{id}/cancel` | run (criador) ou manage | cancela |
| `POST /agents/recover` | manage | re-enfileira/falha tarefas interrompidas **da própria organização** e inicia as pendentes |

Respostas: 429 (muitas tarefas ativas na organização), 409 (agente desabilitado / resultado pendente / já finalizada), 404 (inexistente **ou de outra organização**, indistinguíveis).

## 5. Contratos para a interface (etapa futura)

* **Polling**: `GET /agents/tasks/{id}` até `progress.final = true`; `steps?after=<última sequência>` para um log incremental.
* **`status`**: `pending | running | completed | failed | cancelled`. **`error.kind`** (para tradução): `cancelled, timeout, step_limit, tool_limit, loop_detected, invalid_output, invalid_plan, subtask_failed, cost_limit, authorization, agent_disabled, interrupted, internal` e `ai_<tipo do gateway>` (`ai_authentication, ai_quota, ai_rate_limit, ai_timeout, ai_unavailable, ai_not_configured, ai_budget_exceeded, ai_invalid_response …`, os mesmos tipos da Central de IA).
* **Resultado**: `{"kind": "single", "agent", "output"}` ou `{"kind": "supervised", "plan", "report", "steps":[{id, agent, output}]}`; em falha pode vir `{"partial": {...}}`. Todo resultado é **proposta para revisão humana** (o endpoint inclui o aviso).
* **Idempotência**: reenviar o mesmo `idempotency_key` devolve a mesma tarefa (não executa de novo).
* **Rotas de modelo**: a Central de IA já edita rotas por nome de operação; `agent.<chave>` e `agent.supervisor` são as operações dos agentes (a tela atual só lista rotas que já existem para elas).
* Os novos `agents:*` aparecem na matriz de permissões com o nome cru até a etapa de interface adicionar os rótulos (o frontend e o i18n foram congelados nesta etapa).

### Contratos das futuras ferramentas Google Ads (`future_tools.py`)

Somente declarações (Pydantic de argumentos/resultado + permissão + exigência de aprovação), todas `status = contract_only`: não executam, não retornam dado, não são oferecidas a nenhum modelo e `get_tool` as recusa.
Leitura: `google_ads.get_campaign_performance`, `get_search_terms_report`, `keyword_ideas`, `get_account_structure`. Mutação: `draft_campaign` (rascunho **PAUSED**, `ads:draft` + revisão humana) e `apply_change_proposal` (`ads:spend` + aprovação de gasto **consumida**; `agent_may_hold = false`: **nenhum agente** pode ter essa ferramenta por conta própria).

## 6. Configuração

`AGENT_TASK_TIMEOUT_SECONDS` (900), `AGENT_LEASE_SECONDS` (240, nunca menor que 3× o timeout do provedor + 30 s), `AGENT_HEARTBEAT_SECONDS` (30), `AGENT_MAX_ATTEMPTS` (3), `AGENT_MAX_ACTIVE_TASKS_PER_ORG` (3), `AGENT_DEFAULT_MAX_COST_USD` (2.0; o teto por tarefa é no máximo 10×), `AGENT_MAX_OBJECTIVE_CHARS` (4000), `AGENT_RECOVER_ON_STARTUP` (true).

## 7. Testes

| Verificação | Resultado |
|---|---|
| Backend, suíte completa (PostgreSQL descartável, migrações do zero até o novo head, RLS) | **802 passaram** (619 anteriores + 183 novos) |
| Backend, **modo de banco seguro** (login dedicado sem privilégios, `APP_ENV=production`) | os 183 testes de agentes passam (inclui a recuperação com contexto de plataforma) |
| `test_agents_core.py` (49) | registro (6 agentes, versões e hashes **fixados**, nenhuma ferramenta com efeito colateral, contratos Google Ads só contrato), schemas de saída (RSA 30/90, veredito coerente, limitações obrigatórias, plano limitado), regras determinísticas de RSA e de política (EN/PT), redação de segredos, detecção de injeção, isolamento de conteúdo não confiável, ferramentas |
| `test_agents_runtime.py` (68) | **orquestração** (planejar→delegar→consolidar, dependências, plano inválido reparado/falho, agente desabilitado, falha de subtarefa com resultado parcial), **tool-calling** (ida e volta, argumentos inválidos, ferramenta fora da lista, permissão negada, tenant nas ferramentas, falha de ferramenta sem vazar), **injeção e segredos** (marcação, auditoria, página não consegue acionar ferramenta proibida, redação no que o modelo vê e no que é gravado), **limites** (loop, passos, ferramentas, tempo, saída inválida com um reparo, custo), **erros do gateway** (autenticação, cota, orçamento, não configurado, limite de taxa), **cancelamento**, **papel rebaixado**, **ciclo de vida**, **exclusividade do claim** (2 threads), ***fencing*** (lease perdida), idempotência, teto de tarefas ativas sob **8 requisições concorrentes**, e **recuperação de tarefas interrompidas** (processo "morto" no meio da execução, lease, retomada sem refazer o que terminou, tentativas esgotadas, cancelamento, escopo por organização, retomada automática) |
| `test_agents_api.py` (40) | matriz de **RBAC** por papel, não autenticado, auditoria de recusas, iniciar/consultar/resultado/passos, plano supervisionado, validações, idempotência, 429, segredos colados, cancelamento (criador/admin), configurações com tetos, **isolamento entre organizações** (API e SQL direto) |
| `test_agents_db.py` (12) | RLS em 4 tabelas, escrita cruzada recusada, FK composta, política de plataforma restrita a `agent_tasks` (e flag forjada sem assinatura nada concede), `agent_steps` *append-only*, constraints, cascata |
| `test_agents_gateway_integration.py` (14) | **AI Gateway real** com formatos de fio dos 3 provedores simulados: tool-calling OpenAI, fluxo supervisionado na credencial da organização, **rota por agente** em outro provedor, **fallback**, sem credencial, modelo sem suporte a ferramentas, chaves separadas por organização, uso sem prompt, **orçamento** (bloqueio, liquidação, **duas tarefas concorrentes disputando o orçamento: exatamente uma passa**), e testes estáticos (nenhum cliente HTTP/adaptador/nome de modelo no código dos agentes) |
| Frontend: lint / testes / build / i18n | **não alterado** nesta etapa: 0 erros de lint (26 avisos, os mesmos), 191 testes, build ok, paridade i18n ok |
| Segurança | `bandit -r app/ -ll -ii`: sem achados; `pip-audit`: sem vulnerabilidades conhecidas (dependências inalteradas) |

Como rodar: `bash scripts/test-backend.sh tests/unit/test_agents_*.py` (banco descartável) ou `scripts/homologate-secure-db.sh tests/unit/test_agents_*.py` (modo seguro).

## 8. Limitações e pendências

1. **Nada homologado com APIs reais.** Falta uma execução, com chaves de teste e autorização, de cada agente em cada provedor (verificar tool-calling real, JSON final e custo).
2. *(Superado pela etapa 02.4: a execução agora ocorre num worker independente, ver `docs/etapa-02-04-agent-worker.md`.)* **Execução dentro do processo da API** (tarefa em segundo plano + recuperação por lease), sem fila externa. Se o processo morrer, a tarefa só continua quando uma recuperação rodar (startup, `POST /agents/recover` ou job). Para volume maior, mover o `execute_task` para um worker dedicado (a API do executor já é independente do HTTP).
3. **Cancelamento** não interrompe uma chamada ao modelo já em voo; a tarefa para antes da próxima.
4. **Execução sequencial** dos passos do plano (sem paralelismo entre especialistas).
5. **Sem dados do Google Ads**: pesquisa de mercado é hipótese; desempenho só analisa dados fornecidos; política é consultiva (regras estáticas). Dependem das ferramentas `contract_only` e da integração com a conta.
6. `web.fetch_page` usa a guarda SSRF existente (somente endereços públicos, sem redirecionamento privado, tamanho limitado) e **não executa JavaScript**.
7. A detecção de injeção é heurística e serve a alerta/auditoria; a proteção é estrutural.
8. O teto de custo por tarefa usa o custo informado pelo gateway; modelo **sem preço** não tem custo conhecido (o teto não o enxerga; o orçamento da organização, porém, falha fechado).
9. Instruções dos agentes em inglês (a resposta segue o idioma do objetivo, padrão pt-BR).
10. Os rótulos `agents:*` da matriz de permissões e as telas dos agentes ficam para a etapa de interface.
11. **Migração `e1f2a3b4c5d6` pendente de aplicação** em ambientes reais.
