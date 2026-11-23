# Etapa 02.6 — Painel de Agentes de IA

Branch: `feat/etapa-02-06-agents-dashboard` (a partir de `feat/etapa-02-05-agent-tools`, `8fece98`).

> ## O que foi e o que NÃO foi homologado
> A interface foi validada **de ponta a ponta contra a pilha real em ambiente local** (frontend + API FastAPI + PostgreSQL local descartável +
> **processo worker real**), mas com o **modelo de IA simulado** (o worker sobe pelo `worker_launcher.py`, que troca o AI Gateway por um
> gateway *scriptado*). Portanto: **nenhuma chamada paga**, **nenhum provedor de IA e nenhuma conta Google Ads foram contatados**, nenhuma
> publicação, nenhum deploy, nenhuma migração em ambiente real (a nova `a3b4c5d6e7f8` só rodou no banco descartável de testes e no banco de
> desenvolvimento **local** em Docker).
> **Não declaro integração externa homologada**: nem provedores de IA nem Google Ads foram exercitados com credenciais reais; tudo o que a tela
> mostra sobre eles vem de dublês e do contrato da API real. Ver pendências (seção 9).

## 1. O que foi entregue

| # | Requisito | Entrega |
|---|---|---|
| 1 | Módulo no menu, design aprovado | Item **AI Agents / Agentes de IA** na barra lateral (visível com `agents:read`), rota `/agents`. Mesmas fichas, tokens âmbar, ícones `lucide-react`, abas, `Dialog`, `ConfirmationModal`, `Toast` e `Badge` das telas aprovadas; nenhuma fonte/cor/biblioteca nova |
| 2 | Supervisor + 6 especialistas, com descrição, finalidade, ferramentas, disponibilidade e limitações reais | Aba **Agentes**: cartão do supervisor e seis cartões; textos, ferramentas, limites e rota de modelo vêm do backend. Disponibilidade calculada dos dados reais (desabilitado / sem modelo / sem chave / limitado sem Google Ads / disponível) |
| 3 | Iniciar tarefas | Formulário: agente (ou "automático: o supervisor planeja"), objetivo (5–4000), contexto JSON opcional, limite de custo opcional (US$), chave de idempotência por envio |
| 4 | APIs reais, sem mocks na aplicação | `lib/agentsApi.js` chama somente `/api/v1/agents` (+ leituras de `/ai` e `/google-ads/connection` para a disponibilidade). Os dublês vivem só em `src/__tests__` |
| 5 | Tarefas pendentes, em andamento, concluídas, canceladas e com erro | Histórico com filtros e contagem por status |
| 6 | Acompanhamento: progresso, etapas, tokens, custo | Detalhe com barra de progresso (`role="progressbar"`, indeterminada enquanto o supervisor planeja), execuções por agente, tokens de entrada/saída, custo em USD e % do teto, linha do tempo de passos (modelo/ferramenta, custo, latência), *polling* 2,5 s |
| 7 | Resultados estruturados, fontes, real × estimativa × hipótese | `ResultView`: relatório, saída de cada especialista, `data_basis` e palavras-chave com selos **Dado real / Estimativa / Hipótese**, fonte e métricas citadas; resumo de proveniência; item sem rótulo é declarado "proposta do modelo"; rótulo desconhecido **nunca** aparece como real; JSON bruto à parte |
| 8 | Cancelar, histórico, resultados parciais | Cancelar com confirmação (pendente: imediato; em execução: "solicitado", para antes da próxima chamada); resultados parciais de tarefas que falharam |
| 9 | Configurações e seleção de modelo da Central de IA | Diálogo por agente (habilitar, turnos, tempo, com os tetos do servidor) + a mesma linha de rota da Central de IA (`RouteRow`, operação `agent.<chave>`) |
| 10 | RBAC, isolamento, troca de organização | Ver seção 4 |
| 11 | PT-BR/EN-US incluindo permissões `agents:*` | Namespace `agents.json` (paridade verificada), rótulos `agents:read/run/manage` na matriz de papéis, item de menu |
| 12 | IA indisponível, sem Google, limite financeiro, worker offline — sem dado simulado como real | Seção 3 |
| 13 | 390 / 820 / 1440 px | Seção 6 |

### Mudanças de backend (necessárias para a tela dizer a verdade)
* `GET /agents/supervisor`: cartão do supervisor (sem ferramentas, não desabilitável; `limitations` reais adicionadas ao registro).
* `GET /agents/worker/status` (`agents:read`): `online`, nº de workers, janela de "online" e fila **da organização** (pendentes, em execução, idade da mais antiga). Nenhuma identidade de worker nem conteúdo de tarefa é exposto.
* Migração **`a3b4c5d6e7f8`** (aditiva, reversível): tabela não-tenant `agent_workers` (id, status, timestamps, 2 contadores). O worker faz *upsert* a cada `AGENT_WORKER_BEAT_SECONDS` (10 s) e marca `stopped` no desligamento gracioso; "online" = visto há < `AGENT_WORKER_ONLINE_SECONDS` (45 s). Sem isso a tela só poderia **adivinhar** que não há worker.
* `RoutingPanel.jsx` (Central de IA) apenas **exporta** `RouteRow` e aceita `label`: nenhuma mudança visual.
* Ajuste de UX achado no E2E: o painel de detalhe rola para a vista só em layout empilhado (< xl) e com `scroll-mt-24`, para não ficar sob os *toasts* que cobriam o botão "Cancelar tarefa".

## 2. Arquitetura da interface

```
pages/AIAgents.jsx   (abas Agentes | Tarefas, ?tab= & ?task= na URL; carrega agentes, supervisor, ferramentas; setup; worker a cada 15 s)
 ├─ ReadinessAlerts      worker offline · sem chave/modelo · sem rota · limite de gasto · sem Google Ads
 ├─ AgentsCatalog → AgentCard · AgentDetailDialog · AgentSettingsDialog (+ RouteRow da Central de IA)
 └─ TasksPanel (histórico, polling 4 s/20 s) → NewTaskForm · TaskList · TaskDetail (polling 2,5 s) → StepsTimeline · ResultView
lib/agentsApi.js  cliente + helpers (normalização, progresso, custo, contexto JSON, idempotência, proveniência, resultado de ferramenta)
lib/agentAvailability.js  disponibilidade por agente + alertas; "desconhecido" nunca vira problema nem "ok"
lib/usePolling.js   sem sobreposição, pausa com a aba oculta, para ao desmontar
lib/useBackendText.js  texto do backend traduzido só enquanto igual ao en-US
```

## 3. Estados honestos (item 12)

| Situação | O que a tela faz |
|---|---|
| **Worker offline** (`agent_workers` sem batimento recente) | Faixa vermelha com o tamanho da fila; aviso no formulário; tarefa pendente diz "nenhum worker está em execução, não pode começar"; some sozinha quando um worker reporta |
| Status do worker ilegível | **Nenhuma** afirmação (nem "offline" nem "online") |
| **IA indisponível** (`GET /agents` falha) | Tela de erro com "tentar novamente"; **nenhum** cartão/dado é exibido no lugar |
| Sem chave de provedor / sem modelo / sem rota | Faixa + selo "Sem chave de provedor" / "Sem modelo de IA" e botão "Executar" desabilitado com a razão |
| **Limite financeiro** | Faixa quando um orçamento da organização está esgotado; erro `ai_budget_exceeded`/`cost_limit` traduzido com o próximo passo (link à Central de IA / novo limite) |
| **Sem conta Google Ads** | Faixa + selo "Limitado: sem conta Google Ads" nos agentes que leem a conta; aviso no formulário; no passo da linha do tempo a ferramenta aparece **"Indisponível · not_connected"** (o agente recebeu "sem dado", não números inventados) |
| Tarefa com falha parcial | Erro traduzido + resultados parciais dos passos concluídos |
| Falha transitória | "Tentativa 2 de 3 agendada para …; passos concluídos não são repetidos" |
| Texto do backend alterado desde a tradução | Mostrado como recebido (nunca uma tradução possivelmente desatualizada) |
| Resultado sem rótulos / rótulo desconhecido | "Nenhum item tem rótulo de dado: trate como proposta do modelo"; desconhecido = Hipótese |

## 4. RBAC e multi-tenant (item 10)

* `can('agents:read')` mostra o módulo; `agents:run` mostra formulário/execução/cancelamento; `agents:manage` mostra Configurações; `ai:read`/`ai:manage` decidem o que o diálogo de modelo permite. **A interface só decide o que MOSTRAR; o servidor autoriza cada chamada** (403 vira mensagem, não é engolido).
* Cancelar: criador (com `agents:run`) ou administrador (`agents:manage`), como no servidor.
* **Troca de organização:** o `TenantBoundary` desmonta e remonta a página; todo estado (tarefas, resultados, configurações) é descartado; respostas que chegam depois da troca são descartadas pela camada de tenant; `?task=<id>` de outra organização vira **"Tarefa não encontrada"** (404 do servidor), sem vazar nada.
* Papel sem `agents:read` → "sem acesso", **sem** nenhuma chamada de dados (nem `/ai`, nem `/google-ads`).
* Nada de tarefa (objetivo, contexto, resultado) vai para `localStorage`/`sessionStorage` (teste).
* Polling cessa ao desmontar, ao esconder a aba e quando a tarefa termina.

## 5. Testes e evidências

| Verificação | Resultado |
|---|---|
| **Fluxos completos das telas** (`AIAgents.test.jsx`, 43 testes): provedor/ator/organizações reais do app em jsdom contra o backend simulado com o contrato da API real | catálogo, disponibilidade, detalhes, indisponibilidade/retry, alertas, iniciar tarefa (validação local, corpo enviado, idempotência reutilizada após falha, 429), RBAC (analista/gerente/admin/viewer), histórico/filtros, progresso/custo/etapas, polling até concluir, worker offline/retry, 404, resultados real×estimativa×hipótese, parcial + erro, cancelar (pendente/em execução/permissões), configurações + rota de modelo, isolamento A/B, PT-BR, matriz de permissões, teclado/a11y |
| **Contrato com a API real** | `agentsContract.json` verificado contra o backend real (`test_agents_frontend_contract.py`: todos os endpoints, campos lidos pela tela, chaves proibidas) **e** contra o dublê (`agentsContract.test.js`); `agentsCatalog.json` (textos/ferramentas) comparado com o registro real |
| Unitários de `lib/` | cliente (verbos, URLs, erros), normalização, progresso/custo, JSON de contexto, idempotência, proveniência, resultado de ferramenta, disponibilidade, `usePolling` (sem sobreposição, pausa, erro) |
| Paridade i18n | `npm run test:i18n`: sem chaves faltando; varredura de rotas PT-BR/EN-US inclui `/agents` e `/agents?tab=tasks&task=…` + diálogos (detalhes, configurações, cancelar) |
| **E2E na pilha real local** (`frontend/tests/e2e/agents-dashboard.e2e.mjs`, Edge headless) | **32/32**: login real, menu, catálogo com textos reais do backend, worker offline real, tarefa pendente com aviso, cancelamento pendente pela API real, **worker real** subindo e concluindo a tarefa com a UI atualizando sozinha, resultado com proveniência, cancelamento em execução (worker para antes da próxima chamada), erro de IA não retentável traduzido + próximo passo, histórico com todos os status, desabilitar/reabilitar agente, 390/820/1440 sem rolagem horizontal, alvos de toque ≥ 44 px, troca para PT-BR |
| **Regressão visual das telas aprovadas** (`approved-screens-regression.mjs`; build de referência `8fece98` × novo; 19 rotas × 390/820/1440 px; API mockada com organização real) | sem permissão de agentes: **57/57 idênticas (0 px)**; com `agents:*`: **57/57 sem diferença visível** (o item novo fica abaixo da dobra da barra rolável) — nenhuma tela aprovada mudou. Rotina padrão da skill (`visual-regression.mjs`): 21/21 idênticas |
| Frontend completo | lint **0 erros** (26 avisos, os mesmos de antes) · **263** testes unitários · i18n 99 · build ok |
| Backend | suíte completa e modo de banco seguro: ver seção 7 |
| Evidências visuais | `docs/evidence/etapa-02-06/*.png` (catálogo com worker offline, tarefa concluída com proveniência, falha, 390 e 820 px, PT-BR) |

Como reproduzir o E2E (local): subir o PostgreSQL de desenvolvimento (`docker compose up -d postgres`), `python init_db.py`, a API (`uvicorn`), o frontend (`npm run dev`) e:
`PUPPETEER_DIR=<pasta com puppeteer-core> OUT=<pasta> node frontend/tests/e2e/agents-dashboard.e2e.mjs`. O script **recusa** ambientes não-locais, cria uma chave de provedor falsa, um modelo e uma rota na organização do usuário admin e cancela tarefas pendentes antigas.

## 6. Responsividade e acessibilidade

* 390 / 820 / 1440 px: cartões em 1/2/3 colunas; formulário e detalhe lado a lado só ≥ xl, empilhados abaixo (detalhe primeiro quando há tarefa aberta); abas rolam horizontalmente dentro do próprio container; **nenhuma rolagem horizontal da página** (E2E, 6 verificações).
* Alvos de toque ≥ 44 px (`min-h-[44px]`), foco visível (`focus-visible`), abas com setas/Home/End (`role=tablist`), `aria-pressed` nos filtros, `role=progressbar`, `role=alert/status` nas mensagens, rótulos visíveis e erros ligados aos campos (`aria-invalid`), diálogos com *focus trap* e Esc, `prefers-reduced-motion` respeitado (animações com `motion-reduce:`), significado nunca só por cor (texto + ícone em cada selo).

## 7. Resultados dos testes automatizados

| Verificação | Resultado |
|---|---|
| Backend, suíte completa (PostgreSQL descartável, migrações do zero até `a3b4c5d6e7f8`, RLS) | **973 passaram, 1 ignorado** (SIGTERM em POSIX, no Windows); cobertura 77,1%. A execução completa acusou 1 falha de arquitetura (a API importava o módulo da fila de *claim* só para ler a liveness); corrigido movendo `beat`/`online_workers` para `app/agents/liveness.py` (a fila segue exclusiva do worker) e reexecutado: worker + dashboard + contrato = 70 passaram |
| Novos testes de backend | `test_agents_dashboard_api.py` (18: cartão do supervisor, RBAC, status do worker, liveness em laço real, tabela sem dado de tenant), `test_agents_frontend_contract.py` (2: contrato e snapshot do catálogo contra a API/registro reais) |
| **Modo de banco seguro** (login sem privilégios, `APP_ENV=production`) | 76 passaram, 1 ignorado (worker, processos separados, dashboard, contrato) |
| Migração | ida/volta/ida na suíte de migrações (head `a3b4c5d6e7f8`); aplicada só no banco local descartável |
| Segurança | `bandit -r app/ -ll -ii`: sem achados (exit 0); dependências do backend inalteradas (`pip-audit` da etapa anterior: sem vulnerabilidades); sem novos pacotes no frontend |
| CI | passo do modo seguro ampliado com os dois arquivos novos |

Como rodar: `bash scripts/test-backend.sh tests/unit/test_agents_dashboard_api.py tests/unit/test_agents_frontend_contract.py` e, no frontend, `npm run test:unit && npm run test:i18n`.

## 8. Segurança

* Sem novos pacotes. `bandit -r app/ -ll -ii` e `pip-audit` (ver seção 7); `npm audit` no CI.
* Sem `alert()`/`confirm()` (usa `useToast` e `ConfirmationModal`); sem `dangerouslySetInnerHTML`; todo texto de modelo/ferramenta é renderizado como texto (React escapa); conteúdo de ferramentas aparece em `<pre>` já redigido/truncado pelo servidor.
* A tela nunca recebe: instruções dos agentes (prompts), tokens, chaves, `lease_owner`, `worker_id`, `idempotency_key` (verificado pelo contrato).

## 9. Pendências e limitações (honestas)

1. **Integrações externas não homologadas.** Nenhum provedor de IA e nenhuma conta Google Ads reais foram usados; o E2E usa gateway scriptado e a tela de "sem Google Ads" é o único caminho exercitado de ponta a ponta. A exibição de dados reais de Google Ads (selos "Dado real"/"Estimativa") foi validada com resultados simulados no contrato, não com respostas reais.
2. O item de menu fica abaixo da dobra da barra lateral em telas de ~900 px de altura (a barra já rola; mesmo comportamento dos itens existentes). Não foi reposicionado para não alterar o layout aprovado.
3. O histórico mostra as **100 tarefas mais recentes** (limite da API, sem paginação); filtros e contagens valem para essas 100.
4. Cancelar uma tarefa em execução **não interrompe uma chamada de modelo já em voo** (limitação da etapa 02.3): a interface diz isso na confirmação; uma tarefa cuja resposta final chega antes da checagem termina como concluída.
5. Os textos de agentes/ferramentas são do backend (inglês) e só são traduzidos enquanto iguais ao catálogo; a mudança de um texto exige atualizar `agentsCatalog.json` e `agents.json` (um teste do backend falha até isso ser feito).
6. "Worker online" é inferido do batimento (45 s): um worker morto continua "online" por até 45 s; o estado global não distingue workers por organização.
7. O E2E real roda manualmente (precisa de navegador, banco e backend locais) e **não** está no CI; o CI cobre as suítes de backend (incluindo contrato e modo de banco seguro), testes de frontend/i18n/build e segurança.
8. Sem tela de operação do worker/fila além do estado agregado e sem exportador de métricas.
