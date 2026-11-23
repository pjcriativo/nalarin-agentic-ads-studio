# Etapa 02.2 — Central de Inteligência Artificial

Branch: `feat/etapa-02-02-ai-control-center` (a partir de `feat/etapa-02-01-ai-gateway`, `42950be`).

> **Status de homologação:** a interface conversa **somente** com os endpoints reais `/api/v1/ai` (nenhum mock na
> aplicação). Porém **todos os testes usam respostas simuladas**: o backend nos testes Python fala com provedores
> simulados (`httpx.MockTransport`) e o E2E de navegador roda sobre um servidor de API em memória. **Nenhum provedor
> (Anthropic, Gemini, OpenAI) foi homologado com chave real e nenhuma chamada paga foi feita.** Nenhuma migração foi
> executada em desenvolvimento ou produção (apenas no PostgreSQL descartável dos testes).

## 1. O que foi entregue

### Telas (rota `/ai`, item "Central de IA" na lateral para quem tem `ai:read`)

| Aba | Conteúdo |
|---|---|
| **Provedores** | Um cartão por provedor (Anthropic, Google Gemini, OpenAI): estado (não configurado, conexão verificada, falha na verificação, ainda não testada, chave revogada), **apenas os 4 últimos caracteres**, data do último teste. Ações: adicionar chave, testar conexão, substituir, revogar (com confirmação). |
| **Modelos** | Catálogo da organização: capacidades (ferramentas, visão, JSON), preços de entrada/saída/**cache** (USD por 1 mi de tokens), ativo/inativo (interruptor), editar, excluir (com confirmação). **Descobrir modelos** lista os nomes que o próprio provedor devolve para a chave da organização. |
| **Roteamento** | Por operação (padrão, gerar texto, reescrever campo, analisar modelo, recriar anúncio): modelo principal + fallbacks reordenáveis por teclado. Explica que o fallback só ocorre em limite de taxa, tempo esgotado ou indisponibilidade. |
| **Consumo e limites** | Limites diário/mensal (barra de progresso, gasto, em andamento, restante), alertas a partir de 80% e ao atingir o limite, KPIs (requisições, tokens de entrada/saída/**em cache**, custo em USD, falhas), custo por dia, agrupamentos por modelo/tarefa/usuário e histórico recente. |

Estados tratados: carregando, **serviço indisponível** (com "Tentar novamente"), **sem permissão**, não configurado,
conexão testada/falhou, **limite atingido** (alerta de papel `alert`), vazio, erro de validação por campo e erro de
servidor traduzido (nunca o corpo do provedor).

Design: nenhum token novo. Reutiliza paleta âmbar, cartões brancos com borda cinza, ícones `lucide-react`, `ConfirmationModal`,
`useToast`, `useFocusTrap` e o padrão de diálogo da matriz de permissões (portal, blur, `Esc`). Skills aplicadas:
`frontend-design-director` → `ui-ux-pro-max`, `frontend-design`, `react-best-practices`, `web-design-guidelines`.

### Segurança da chave (BYOK) no navegador

* A chave digitada vive só no estado do diálogo: `type="password"`, `autocomplete="off"`, nunca pré-preenchida, apagada ao fechar.
* Trafega uma única vez (criar/substituir). Nenhuma resposta da API a contém; a UI mostra `last4`.
* Testes provam que a chave não está no DOM, no `localStorage`/`sessionStorage` nem em respostas posteriores.

### RBAC e isolamento multi-tenant

* `ai:read` (analista+) vê tudo; `ai:manage` (admin, owner) gerencia. Gerente tem `ai:use` mas **não** gerencia. Visualizador não vê a tela, não vê o item de menu e **não dispara nenhuma requisição `/ai`**.
* A UI só decide o que **mostrar**; cada requisição é autorizada pelo servidor (um 403 é exibido, não engolido).
* Troca de organização: o `TenantBoundary` remonta a página; os testes provam que nada da organização anterior permanece e que cada chamada leva exatamente um `X-Organization-Id`.

### i18n (PT-BR + EN-US)

Novo namespace `ai` (228 chaves por idioma, paridade total), item de menu e **rótulos `ai:*` da matriz de permissões** (`ai_read`, `ai_use`, `ai_manage`, grupo "IA"), que antes apareciam crus. Varredura de rotas em ambos os idiomas inclui `/ai`.

## 2. Correções técnicas no backend

### 2.1 Reservas de orçamento abandonadas

**Problema:** se o processo morresse entre reservar e liquidar, o valor ficava em `reserved_micros` até a janela virar.

**Solução** (migração `d0e1f2a3b4c5`, **não executada** em dev/prod):

* `ai_budget_reservations` (com RLS `tenant_isolation`): uma linha por reserva ativa, gravada na mesma transação da reserva.
* Liquidar/liberar apaga a linha (`DELETE … RETURNING` trava a linha: quem apagar primeiro aplica o efeito, o outro nada faz).
* Reserva mais velha que `AI_RESERVATION_TTL_SECONDS` (padrão 900 s, nunca menor que 5× o timeout) é **convertida em gasto pela estimativa**, **nunca simplesmente liberada**: o provedor pode ter respondido e cobrado antes da queda, então liberar permitiria gastar acima do limite.
* A recuperação roda antes de cada nova reserva e ao listar orçamentos; gera `audit_event` `ai.budget.reservation_recovered`.
* Um `settle` tardio depois da recuperação não cobra duas vezes; reserva de janela anterior não toca a janela nova.

### 2.2 Tokens em cache

`ai_models.cached_input_price_micros_per_mtok` (opcional). Custo = `(entrada − cache) × preço de entrada + cache × preço de cache + saída × preço de saída`.
**Sem preço de cache informado, tokens em cache seguem cobrados ao preço de entrada** (nunca subcontados). A estimativa usa o maior dos dois preços. Nenhum preço é inventado: modelo sem preço continua bloqueado sob limite ativo.

### 2.3 Outras mudanças no backend

* `GET /ai/usage/summary` agora traz `by_day` e `cached_input_tokens`.
* `X-AI-Error` passou a ser exposto pelo CORS (o navegador precisa ler o tipo de erro).
* Corrigido `/ad-remix/reconstruct` (etapa anterior): ver relatório 02.1.

## 3. Endpoints usados pela interface

| Método e caminho | Permissão | Uso |
|---|---|---|
| `GET /ai/providers` | read | estado por provedor |
| `GET /ai/credentials` | manage | lista (só visão pública) |
| `POST /ai/credentials` · `POST …/{id}/test` · `POST …/{id}/rotate` · `DELETE …/{id}` | manage | cadastrar, testar, substituir, revogar |
| `GET /ai/models` · `GET /ai/models/discover?provider=` | read · manage | catálogo, descoberta |
| `POST /ai/models` · `PATCH/DELETE /ai/models/{id}` | manage | CRUD do catálogo |
| `GET /ai/routing` · `PUT/DELETE /ai/routing/{operation}` | read · manage | modelo principal e fallbacks |
| `GET /ai/budgets` · `PUT/DELETE /ai/budgets/{period}` | read · manage | limites |
| `GET /ai/usage/summary` · `GET /ai/usage/logs` | read | consumo e histórico |

## 4. Testes e verificações

| Verificação | Resultado |
|---|---|
| Backend, suíte completa (PostgreSQL descartável, incluindo migrações do zero até o novo head, RLS, concorrência) | **619 passaram** (598 anteriores + 21 novos) |
| `test_ai_control_center_backend.py` (19) | reservas abandonadas (cobra, não libera; nunca acima do limite; `settle` tardio sem cobrança dupla; janela anterior; recuperações concorrentes), preço de cache (com e sem preço, estimativa conservadora, custo via gateway), campos novos da API |
| `test_ai_frontend_contract.py` (2) | todos os campos que a tela lê existem na API real; nenhuma chave em qualquer resposta; `X-AI-Error` exposto por CORS |
| Frontend, testes unitários (Vitest) | **191 passaram** em 11 arquivos |
| `AIControlCenter.test.jsx` (40) | CRUD de chaves (só 4 últimos caracteres, chave nunca no DOM/armazenamento, rejeição do provedor, testar, substituir, revogar com confirmação), CRUD de modelos (preços, cache, validação, duplicado, editar, desativar, excluir), descoberta, roteamento, consumo/limites (alertas 80% e atingido, definir/alterar/desativar/remover), estados (não configurado, pronto, serviço indisponível, erro de salvar), **RBAC** (analista, gerente, visualizador, admin, 403 do servidor), **troca de organização sem vazamento**, persistência após recarregar, PT-BR, rótulos `ai:*` da matriz, funções puras |
| `aiContract.test.js` (1) | o simulador devolve os mesmos campos do contrato compartilhado |
| i18n: `npm run test:i18n` | paridade PT-BR/EN-US; varredura de rotas nos dois idiomas inclui `/ai` (sem chave faltando, sem chave crua, sem inglês no PT-BR) |
| Lint (`eslint .`) | **0 erros**, 26 avisos (mesmo patamar de antes; nenhum novo) |
| Build (`vite build`) | ok |
| E2E de navegador (Edge, `tests/e2e/ai-control-center.e2e.mjs`) | **69/69 em en-US e 69/69 em pt-BR** (3 viewports): fluxo completo, ausência de rolagem horizontal, diálogos dentro da tela, nomes acessíveis, alvos de toque, setas no tablist, papel somente leitura, visualizador, serviço indisponível |
| Regressão visual (20 rotas aprovadas × 3 tamanhos) | 1440 e 820 px: **0 px de diferença**; 390 px: difere pelo conserto do shell (seção 5) |
| Segurança | `bandit -r app/ -ll -ii`: sem achados. Dependências inalteradas nesta etapa (último `pip-audit`, etapa 02.1: sem vulnerabilidades conhecidas) |

Observação sobre a regressão visual: na primeira tentativa as duas compilações carregavam a tela de login (a variável `VITE_API_URL=/api/v1` do build de produção escapava do interceptador), o que tornava a comparação vazia. Foi refeita com `VITE_API_URL=http://localhost:8000/api/v1` e conferida visualmente (telas reais renderizadas).

## 5. Evidências

`docs/evidence/etapa-02-02/`: capturas reais (Edge) em 1440, 820 e 390 px nos dois idiomas (provedores, modelos, roteamento, consumo, somente leitura, sem acesso, serviço indisponível) e o resumo da regressão visual.

### Correção do layout móvel (necessária, restrita a < 768 px)

Ao medir o 390 px descobri um bug **existente desde a versão inicial**: no `<aside>` do `Layout`, `relative` vence `fixed` no CSS gerado,
então a barra lateral "escondida" continuava ocupando 256 px no fluxo e empurrava todo o conteúdo (qualquer tela ficava numa coluna de ~130 px).
Sem corrigir isso nenhuma tela, inclusive a Central de IA, seria utilizável no celular. A correção é uma classe (`max-md:!fixed`) e **não altera nada a partir de 768 px**
(1440 e 820 px: 0 pixels de diferença em 20 rotas aprovadas). Em 390 px as telas aprovadas mudam justamente por esse conserto — **decisão a confirmar** (ver seção 6).

## 6. Limitações e pendências

1. **Nenhum provedor homologado.** Falta rodar, com chaves de teste e autorização, a verificação mínima de cada provedor (criar chave com verificação, descobrir modelos, uma chamada curta).
2. **E2E de navegador usa a API simulada** (`fakeBackend`/`fakeAiBackend`). Para não divergir do servidor real há um **contrato compartilhado** (`frontend/src/__tests__/aiContract.json`) verificado contra o backend real (`test_ai_frontend_contract.py`) e contra o simulador (`aiContract.test.js`). Um E2E de navegador contra o backend real (uvicorn + PostgreSQL descartável) não foi montado.
3. **Migração `d0e1f2a3b4c5` pendente de aplicação** em ambientes reais; recomenda-se ensaiar em cópia.
4. **Correção do `Layout` em 390 px muda a aparência móvel das telas aprovadas** (corrige o bug). Se preferirem manter o comportamento antigo, reverter essa única classe; a Central de IA então só será usável em ≥ 768 px.
5. A tela de **Brands** tem excesso horizontal próprio em 390 px (botão "Add Brand" cortado): fora do escopo, não alterado.
6. A **descoberta de modelos** só confirma o nome e a disponibilidade; capacidades e preços são informados por quem cadastra (o provedor não os fornece de forma confiável pela listagem).
7. Tokens de **escrita em cache** (cobrança diferenciada de alguns provedores) ainda não têm preço próprio; só leitura de cache é distinguida.
8. A tabela de "Requisições recentes" mostra o id curto do usuário (não há endpoint de nomes); o gráfico de custo por dia é de barras CSS (sem biblioteca).
9. Fora do escopo desta etapa e mantido assim: agentes.
