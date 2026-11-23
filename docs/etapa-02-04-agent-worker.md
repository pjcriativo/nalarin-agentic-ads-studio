# Etapa 02.4 — Worker de agentes

Branch: `feat/etapa-02-04-agent-worker` (a partir de `feat/etapa-02-03-agent-orchestration`, `25507ed`).

> **Status de homologação:** toda a validação usa provedores **simulados** (gateway *scriptado* nos testes do worker; `provider_mock`
> nos testes de integração do gateway da etapa anterior). **Nenhuma chamada paga foi feita**, nenhuma migração foi executada em
> ambiente real (a `f2a3b4c5d6e7` só rodou no PostgreSQL descartável dos testes), **nenhum deploy** foi feito, o frontend **não foi alterado**
> e **não há Google Ads** nesta etapa. Os agentes continuam só lendo e planejando.

## 1. O que mudou

| Antes (02.3) | Agora (02.4) |
|---|---|
| `POST /agents/tasks` agendava `execute_task` como *background task* **dentro do processo da API**; no startup a API recuperava tarefas | A API **só registra** a tarefa (`pending`) e consulta resultados. **Nenhum** caminho HTTP executa agente. Sem recuperação no startup da API |
| Se a API morresse, a tarefa só andava quando alguém rodasse a recuperação | O **worker** (`python -m app.worker`, processo independente) consome a fila, renova a lease, recupera tarefas de workers mortos e faz *retry* controlado |
| Limite de tarefas só na criação (`AGENT_MAX_ACTIVE_TASKS_PER_ORG`) | Mantido **e** limites de **execução simultânea** global e por organização, valendo entre todos os workers |
| Falha transitória do provedor = tarefa `failed` | Falha transitória = volta à fila com *back-off* exponencial até `max_attempts`; retomada **sem refazer** o que já terminou |

O executor, o supervisor, os agentes, o runner, as ferramentas e o AI Gateway **não foram duplicados nem reescritos**: o worker chama
`executor.execute_task` (a mesma função de antes). As únicas mudanças no núcleo foram cirúrgicas (seção 3).

## 2. Arquitetura

```
 cliente ──► API (FastAPI)                          PostgreSQL (fila persistente + RLS)              agent-worker (N processos)
              POST /agents/tasks ──INSERT pending──► agent_tasks ◄──claim atômico (SKIP LOCKED)────── app.agents.queue.claim_next
              GET  /agents/tasks/{id}[/result] ◄──── agent_tasks / agent_runs / agent_steps          │  limites global + por org
                                                       ▲                                             ▼
                                                       └──── escritas com fencing (lease_owner) ◄── executor.execute_task
                                                                                                     ├─ supervisor / especialistas / runner
                                                                                                     └─ AI Gateway (credencial, rota, orçamento da ORG)
```

### 2.1 Fila no PostgreSQL (sem Redis)

* **Claim atômico** (`app/agents/queue.py`): um único `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)` transforma a tarefa
  `pending` mais antiga e *claimable* em `running`, com `lease_owner` novo (o *token de fencing*), `lease_expires_at`, `attempts + 1` e
  `worker_id`. Dois workers nunca pegam a mesma linha.
* **Limites de concorrência** (contagem de linhas `running` com lease não vencida, portanto válidos **entre todos os processos**):
  `AGENT_GLOBAL_MAX_RUNNING` (padrão 8) e `AGENT_ORG_MAX_RUNNING` (padrão 2). A checagem + o claim rodam sob um
  `pg_advisory_xact_lock` de milissegundos (a execução nunca fica sob o lock), para que dois workers não vejam "sobrou uma vaga" ao
  mesmo tempo. Uma organização barulhenta não bloqueia as demais: as tarefas excedentes ficam `pending` e as de outras organizações passam na frente.
* **Claimable** = `pending`, sem `cancel_requested` e com `available_at` vencido (*back-off* de retry). Ordem: mais antiga primeiro.
* **Por que não Redis:** o volume esperado (dezenas de tarefas longas, minutos cada) é muito menor que o que o PostgreSQL sustenta com
  `SKIP LOCKED`; a fila já é transacional com o resto do estado (custos, passos, auditoria), o que dá *claim*, lease e fencing sem uma segunda fonte de verdade.
  **Critério para reavaliar:** milhares de tarefas por minuto, ou latência de *claim* medida acima de ~1 s sob carga real.
* **Migração `f2a3b4c5d6e7`** (aditiva, reversível): `agent_tasks.available_at`, `agent_tasks.worker_id` e o índice parcial `ix_agent_tasks_claimable`
  (`WHERE status = 'pending'`). Nenhuma tabela nem política nova: RLS `tenant_isolation` e a política `platform_recovery` já cobrem as colunas.

### 2.2 O worker (`app/worker/`)

`python -m app.worker` · `--once` (recupera, drena o que for *claimable* e sai) · `--healthcheck` (exit 0/1, usado pelo `HEALTHCHECK` do Docker).

Laço: **recuperar** (a cada `AGENT_RECOVERY_INTERVAL_SECONDS`) → **preencher vagas** (`AGENT_WORKER_CONCURRENCY` por processo) → **dormir** até
uma tarefa terminar, um sinal chegar ou `AGENT_POLL_INTERVAL_SECONDS` passar.

* **Heartbeat:** toda tarefa em execução renova a lease por duas vias (a cada chamada ao modelo e por uma corrotina de heartbeat com
  sessão própria, `AGENT_HEARTBEAT_SECONDS`). Lease = `AGENT_LEASE_SECONDS` (≥ 3× o timeout do provedor + 30 s).
* **Recuperação automática:** todo worker roda `recover_interrupted_tasks()` periodicamente: lease vencida ⇒ volta a `pending`
  (ou `failed/interrupted` ao esgotar `max_attempts`; `cancelled` se havia cancelamento pedido). Idempotente e seguro com vários
  workers ao mesmo tempo (cada linha é atualizada por exatamente um).
* **Retries controlados** (`executor.execute_task` → `TaskStore.fail_or_retry`, uma instrução com fencing): só causas **transitórias**
  (`errors.RETRYABLE_KINDS`: `ai_unavailable`, `ai_timeout`, `ai_rate_limit`, `internal`). A tarefa volta a `pending` com
  `available_at = agora + min(AGENT_RETRY_MAX_SECONDS, AGENT_RETRY_BASE_SECONDS × 2^(tentativa−1))` até `max_attempts`; depois, `failed` com o `error.kind`.
  **Nunca** são repetidos: autenticação, cota, orçamento, não configurado, saída inválida, limite de passos/ferramentas/custo, autorização,
  timeout da própria tarefa (repetir só gastaria mais).
* **Desligamento gracioso** (SIGTERM/SIGINT): para de reivindicar → espera as tarefas por `AGENT_SHUTDOWN_GRACE_SECONDS` → as que
  não terminaram são **devolvidas à fila sem consumir tentativa** (`attempts − 1`, sem back-off, auditoria `agent.task.released`) e outro worker as retoma de imediato. Um `kill -9` é coberto pela lease.
* **Health:** `GET /healthz` em `AGENT_WORKER_HEALTH_HOST:AGENT_WORKER_HEALTH_PORT` (padrão `127.0.0.1:8081`, `0` desliga). 200 quando o
  laço de polling bateu há menos de `AGENT_WORKER_STALE_SECONDS` e o banco respondeu; 503 se banco com erro, laço parado ou worker em
  desligamento. O corpo traz só contadores (`status, worker, active_tasks, concurrency, database, last_poll_seconds_ago, uptime_seconds`).
* **Logs estruturados** (`app/worker/logs.py`): uma linha JSON por evento (`worker.started|stop_requested|draining|stopped|recovered|db_error`,
  `task.claimed|finished`). Só ids, contadores, durações, status e *kinds* de erro. **Nunca** prompt, objetivo, entrada/saída de ferramenta, resposta do modelo,
  credencial ou DSN; texto livre passa por `redact_secrets`; exceção é logada **somente pelo tipo** (sem mensagem, sem *traceback*).
* **Pré-voo:** o worker recusa subir se o papel do banco puder contornar RLS (`verify_runtime_role_is_safe`, o mesmo guard da API).

## 3. Mudanças no núcleo (mínimas)

| Arquivo | Mudança |
|---|---|
| `agents/executor.py` | `execute_task(task_id, org_id, owner=None)`: com `owner` usa a lease já tomada pelo worker (sem owner, reivindica sozinho, como antes). Fim de tentativa com erro passa por `fail_or_retry`. Removidos `schedule()` e `recover_and_resume()` (a API não agenda mais nada). Antes de rodar, recusa tarefa **já no teto de custo** (um retry não zera o teto). Log de falha inesperada só com o **tipo** da exceção |
| `agents/executor.py` | **Início fenceado:** o fechamento de execuções órfãs e a gravação do prazo agora exigem `lease_owner` (um executor com lease antiga que acorda tarde devolve `lost_lease` e **não** mexe nas execuções do dono atual) |
| `agents/store.py` | `fail_or_retry` (retry/fail/cancel numa instrução fenceada) e `release` (devolução graciosa) |
| `agents/queue.py` (novo) | `claim_next`, `release`, `depth` |
| `api/v1/agents.py` | Sem `BackgroundTasks`. `POST /recover` só re-enfileira (resposta: `queued`, antes `started`). Tarefas expõem `next_attempt_at` |
| `main.py` / `config.py` | Sem recuperação no startup (`AGENT_RECOVER_ON_STARTUP` removida); novas variáveis do worker |

## 4. Segurança e multi-tenant

* **Claim em contexto de plataforma, execução em contexto da organização.** O claim usa a única política extra de `agent_tasks`
  (`platform_recovery`, só satisfeita pelo contexto de administrador de plataforma **assinado** com `SECRET_KEY`); ele só seleciona ids e muda colunas de estado/lease e devolve
  apenas `{task_id, org_id, owner, attempt}`. A execução abre sessão **vinculada à organização da tarefa** (RLS `tenant_isolation`), usa o **AI Gateway da própria organização**
  (credenciais BYOK, rota, fallback, **orçamento**) e as ferramentas filtram por `organization_id` além do RLS.
* **Mesmo papel de banco da API** (não-bypass). O worker precisa de `DATABASE_URL`/`RUNTIME_DATABASE_URL`, `SECRET_KEY` e `OAUTH_TOKEN_ENCRYPTION_KEY` **iguais** aos da API; não precisa de `FACEBOOK_*`, R2, nem chave de IA de servidor (não existe).
* **RBAC reavaliado na execução:** usuário rebaixado entre o pedido e a execução ⇒ `failed/authorization`, sem chamada ao modelo. As ferramentas continuam checando a permissão a cada chamada.
* **Fencing em todas as escritas**, inclusive no início da execução (seção 3) e no *release*/*retry*: o dono antigo nunca avança, devolve nem finaliza uma tarefa reassumida.
* **Limites financeiros:** teto por tarefa (`max_cost_micros`) vale **entre tentativas** (custo acumulado no banco; uma retomada já acima do teto não faz chamada paga), orçamento por organização continua no AI Gateway (reserva → liquidação). Retry nunca repete erro de cota/orçamento.
* **Auditoria** (`audit_logs`, sem conteúdo): além das de 02.3, `agent.task.retry_scheduled` e `agent.task.released`.
* **Somente leitura/planejamento:** nada nesta etapa permite publicar campanha ou gerar gasto.

## 5. Operação (Docker / Coolify)

### Serviços

| Serviço | Imagem | Comando | Observações |
|---|---|---|---|
| `backend` (API) | `backend/Dockerfile` | `alembic upgrade head && uvicorn …` | **Único** que roda migrações |
| `agent-worker` | `backend/Dockerfile.worker` (mais enxuta: sem Chromium) | `python -m app.worker` | Sem porta publicada; `HEALTHCHECK` = `python -m app.worker --healthcheck`; `stop_grace_period` > `AGENT_SHUTDOWN_GRACE_SECONDS` |

`docker-compose.yml` (dev) e `docker-compose.prod.yml` ganharam o serviço `agent-worker` (a produção espera o `backend` ficar *healthy*).
Escalar: `docker compose -p ads-studio-prod -f docker-compose.prod.yml up -d --scale agent-worker=3` (os limites global e por organização valem entre todas as réplicas).

### Coolify

1. Novo recurso na **mesma** rede/projeto do backend, tipo *Dockerfile*: contexto = raiz do repositório, arquivo `backend/Dockerfile.worker`. **Sem domínio e sem porta exposta.**
2. Variáveis: as mesmas do backend para banco e segredos (`DATABASE_URL` ou `RUNTIME_DATABASE_URL` + `APP_ENV=production`, `SECRET_KEY`, `OAUTH_TOKEN_ENCRYPTION_KEY`, `DB_RUNTIME_ROLE`) e as `AGENT_*` do quadro abaixo.
3. Health check: o do Dockerfile (`--healthcheck`). Em *Graceful shutdown* use ≥ 45 s.
4. **Ordem de implantação:** migrações (dono) → backend (API) → worker. Antes das migrações o worker fica *unhealthy* (erro de banco) e não toca o esquema. Se a API sobe **sem** nenhum worker, as tarefas ficam `pending` (nada se perde).
5. Réplicas: comece com 1 e suba conforme a fila (`GET /agents/tasks?status=pending`).

### Variáveis (padrões)

| Variável | Padrão | Significado |
|---|---|---|
| `AGENT_WORKER_CONCURRENCY` | 2 | tarefas simultâneas **por processo** |
| `AGENT_GLOBAL_MAX_RUNNING` | 8 | tarefas rodando ao mesmo tempo, **todos os workers** |
| `AGENT_ORG_MAX_RUNNING` | 2 | idem, **por organização** |
| `AGENT_MAX_ACTIVE_TASKS_PER_ORG` | 3 | teto de pendentes+rodando por organização (na criação, 429) |
| `AGENT_POLL_INTERVAL_SECONDS` | 2 | intervalo de polling quando ocioso |
| `AGENT_RECOVERY_INTERVAL_SECONDS` | 30 | recuperação de tarefas de workers mortos |
| `AGENT_SHUTDOWN_GRACE_SECONDS` | 30 | tempo para terminar tarefas após SIGTERM |
| `AGENT_RETRY_BASE_SECONDS` / `AGENT_RETRY_MAX_SECONDS` | 5 / 300 | *back-off* `base × 2^(tentativa−1)` limitado ao máximo |
| `AGENT_MAX_ATTEMPTS` | 3 | tentativas por tarefa (fixado na criação) |
| `AGENT_LEASE_SECONDS` / `AGENT_HEARTBEAT_SECONDS` | 240 / 30 | lease e renovação |
| `AGENT_WORKER_HEALTH_HOST` / `_PORT` / `AGENT_WORKER_STALE_SECONDS` | 127.0.0.1 / 8081 / 60 | endpoint de saúde |
| `AGENT_WORKER_ID` | host-pid-aleatório | rótulo nos logs/`worker_id` |

### Diagnóstico

* Fila: `SELECT status, count(*) FROM agent_tasks GROUP BY 1;` (como dono do banco) ou `GET /agents/tasks?status=pending` por organização.
* Tarefa parada em `running` com lease vencida: a recuperação dos workers a re-enfileira em até `AGENT_RECOVERY_INTERVAL_SECONDS`; `POST /agents/recover` (admin da organização) antecipa.
* Tarefa `pending` que não anda: sem worker vivo, organização no limite `AGENT_ORG_MAX_RUNNING`, global no limite, ou em *back-off* (`next_attempt_at`).
* Logs: `docker logs <worker> | jq 'select(.event=="task.finished")'`.

## 6. Testes

Todos contra o PostgreSQL **descartável** (`*_test`), migrações do zero até o novo *head*, gateway scriptado (nenhuma rede).

| Arquivo | Testes | O que prova |
|---|---|---|
| `test_agents_worker.py` | 50 | **Fila:** claim exclusivo sob 8 threads, limite global e por organização sob concorrência, não-inanição entre organizações, vaga liberada, lease vencida não ocupa vaga, *back-off*/cancelamento não-*claimable*, só ids retornam. **Fencing/lease:** dono antigo não avança, não finaliza, não devolve, não fecha execuções do dono atual (`lost_lease`); lease viva nunca é roubada; heartbeat renova a lease durante chamada longa. **Laço:** execução, paralelismo até a concorrência, limites global/por org na prática, worker morto recuperado e retomado, cancelamento (pendente e em voo), papel rebaixado, queda do banco sem derrubar o worker. **Retries:** *back-off* dobra e é limitado, esgota `max_attempts`, erros não-transitórios nunca repetem (6 tipos), retomada sem refazer etapas concluídas, teto de custo não zera, tarefa no teto não faz chamada paga, crash inesperado limitado e sem vazar mensagem. **Desligamento:** termina dentro da graça; excedente devolvido sem gastar tentativa e retomado por outro worker. **Isolamento A/B:** contexto, usuário, gateway, saída, execuções, passos e auditoria de cada organização separados sob concorrência; recuperação/cancelamento não cruzam organizações; papel sem bypass. **Observabilidade:** logs JSON sem objetivo/segredo/*traceback*, formatter só com o tipo da exceção, campos reservados, `/healthz` (200/404/503 e `--healthcheck`). **Arquitetura:** API não executa, worker não duplica lógica nem referencia provedor/modelo, só o worker importa a fila, *head* único da migração |
| `test_agents_worker_processes.py` | 7 (6 no Windows + SIGTERM só em POSIX) | **API (uvicorn) e worker em processos separados** (API só enfileira: tarefa fica `pending` 2 s sem worker; worker processa e a API publica o resultado; API responsiva com worker ocupado). **3 workers** competindo por 15 tarefas de 2 organizações: cada tarefa **exatamente uma vez** (`attempts = 1`, 1 execução, 1 chamada ao modelo), pico observado ≤ limites configurados (3 global / 2 por org) e > 1 (paralelismo real), ≥ 2 processos trabalharam. **Queda e reinício:** `kill` de um worker no meio do copywriter; lease viva **não** é roubada; vencida, outro processo retoma **sem novo plano e sem refazer o analista** (2 chamadas em vez de 4), custo contabilizado uma única vez, auditoria `recovered`. Dois workers vivos não executam a mesma tarefa. **SIGTERM** (Linux): sai com 0, tarefa devolvida sem gastar tentativa, outra instância conclui |
| Testes de 02.3 adaptados | — | a API deixou de executar: os testes que dependiam disso agora enfileiram e rodam o **worker real** (`drain()`); falha transitória/crash agora viram *retry* |

## 7. Evidências

| Verificação | Resultado |
|---|---|
| Backend, suíte completa (PostgreSQL descartável, migrações do zero até `f2a3b4c5d6e7`, RLS) | **858 passaram, 1 ignorado** (o SIGTERM em POSIX, no Windows); 802 anteriores + 56 novos; cobertura total 75,65% (`worker.py` 92%, `executor.py` 94%, `store.py` 96%, `queue.py` 80%) |
| Migração | ida, volta (`downgrade`) e ida de novo na suíte `test_migrations_legacy` (esperado agora `f2a3b4c5d6e7`); *head* único |
| **Modo de banco seguro** (login dedicado sem privilégios, `APP_ENV=production`): worker + agentes | 176 passaram, 1 ignorado (inclui API e workers como processos separados) |
| **Linux** (contêiner `python:3.12-slim-bookworm` contra o PostgreSQL descartável): testes do worker e de processos | **57 passaram, 0 ignorados** (inclui o SIGTERM) |
| Frontend (não alterado): lint / unit / i18n / build | 0 erros (26 avisos, os mesmos) / 191 / 89 / build ok |
| Segurança | `bandit -r app/ -ll -ii`: sem achados (exit 0); `pip-audit -r requirements.txt`: sem vulnerabilidades (dependências inalteradas) |
| CI | passo do modo seguro ampliado com `test_agents_worker.py` e `test_agents_worker_processes.py` |
| Docker | `Dockerfile.worker` e serviço `agent-worker` nos dois compose escritos; **imagem não foi construída nem subida** (sem deploy) |

Como rodar: `bash scripts/test-backend.sh tests/unit/test_agents_worker.py tests/unit/test_agents_worker_processes.py`.

## 8. Limitações e pendências

1. **Nada homologado com APIs reais** (herdado de 02.3): faltam execuções com chaves de teste e autorização para validar *tool-calling*, JSON final e custo em cada provedor.
2. **Migração `f2a3b4c5d6e7` (e `e1f2a3b4c5d6`) pendentes de aplicação** em ambientes reais; a ordem de implantação está na seção 5.
3. **Cancelamento** ainda não interrompe uma chamada ao modelo já em voo (a tarefa para antes da próxima). No desligamento a corrotina local é cancelada, mas a chamada HTTP em voo ao provedor pode já ter sido cobrada: o custo dessa chamada interrompida pode **não** entrar no teto da tarefa, e a reserva de orçamento da organização só é liberada pela recuperação de reservas abandonadas da etapa 02.2. **Esse caminho (cancelar no meio de uma reserva do gateway real) não foi testado nesta etapa.**
4. **Devolução graciosa não consome tentativa**, então uma tarefa que *sempre* ultrapassa a graça e reinicia em loop (worker reiniciando) não chega a `failed` por tentativas; ela ainda é limitada pelo prazo/teto de custo da tarefa e por `AGENT_TASK_TIMEOUT_SECONDS` a cada execução.
5. **Concorrência no processo é cooperativa (um *event loop*)**: o acesso ao banco do executor é síncrono, então muitas tarefas por processo competem pelo laço durante as escritas; prefira várias réplicas com `AGENT_WORKER_CONCURRENCY` baixo a uma réplica com concorrência alta.
6. **Sem *dead-letter* nem painel de fila**: tarefas falhas ficam em `agent_tasks` (`failed`) e são consultáveis pela API; a tela de operação do worker/fila fica para a etapa de interface (frontend congelado).
7. **Métricas:** há logs estruturados e `/healthz`; não há exportador Prometheus (decidir quando houver stack de métricas).
8. **Redis** não é necessário para o volume atual; critério de reavaliação na seção 2.1.
9. O teste de SIGTERM roda só em POSIX (CI Linux e Docker); no Windows o mesmo caminho é coberto pelos testes de desligamento em processo.
10. Tarefas criadas **antes** do deploy do worker (se houver) ficam `pending` até o primeiro worker subir: é o comportamento esperado.
