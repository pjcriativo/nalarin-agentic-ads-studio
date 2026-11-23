# Etapa 02.1 — AI Gateway multiprovedor

Branch: `feat/etapa-02-01-ai-gateway` (a partir de `feat/etapa-01-04-multitenant-homologation`, `d8a1b7a`).

> **Status de homologação:** todos os testes usam **mocks** (`httpx.MockTransport`) com o formato de fio
> dos três provedores. **Nenhuma integração real com Anthropic, Google Gemini ou OpenAI foi homologada.**
> Nenhuma chamada paga foi feita; nenhuma migração foi executada em desenvolvimento ou produção.
> A integração real depende de credenciais de teste e autorização explícita (ver "Pendências").

## 1. O que foi implementado

### Pacote `backend/app/ai/`

| Módulo | Função |
|---|---|
| `types.py` | Contrato neutro: `Message` (partes de texto, imagem, chamada e resultado de ferramenta), `ToolSpec`, `ChatRequest`, `ChatResponse`, `Usage` |
| `errors.py` | `AIError` com `kind` fechado, mensagem pública genérica e status HTTP por tipo; sempre `raise ... from None` |
| `adapters/{anthropic,openai,gemini}.py` | Adaptadores REST sobre `httpx` (sem SDKs). Autenticação só em cabeçalho, sem redirecionamentos, redação de segredos em qualquer mensagem de erro |
| `gateway.py` | `AIGateway`: seleção do modelo, fallback, reserva de orçamento, chamada, contabilização |
| `credentials.py` | BYOK: criar, verificar, testar conexão, rotacionar, revogar; visão pública com apenas os 4 últimos caracteres |
| `budget.py` | Reserva atômica por `UPDATE ... WHERE (gasto+reservado)+estimativa <= limite RETURNING`; janelas diária/mensal UTC |
| `pricing.py` | Dinheiro em micros de USD (inteiros); preço por 1M tokens; custo = ceil(tokens × preço / 1e6) |
| `json_utils.py`, `media.py`, `api_errors.py` | JSON tolerante (cercas markdown), carga segura de imagens (SSRF, assinatura de bytes), mapeamento de erro → HTTP |

### Banco (migração `c9d0e1f2a3b4_ai_gateway.py` — **não executada em dev/prod**)

`ai_credentials`, `ai_models`, `ai_routing`, `ai_budgets`, `ai_usage_logs`: todas com `organization_id`,
`FORCE ROW LEVEL SECURITY` (`tenant_isolation`), constraints (preços ≥ 0, período ∈ {daily, monthly},
uma credencial **ativa** por organização/provedor, FK composta que impede rota apontar para modelo de outra
organização) e índices. `ai_usage_logs` é *append-only*: `UPDATE/DELETE/TRUNCATE` revogados do papel de runtime.

### Catálogo configurável

Nomes de modelo, capacidades (ferramentas/visão/JSON), janelas e **preços são dados** em `ai_models`.
Um teste estático proíbe padrões de nome de modelo em `app/ai`, `api/v1/ai.py`, `copy_generation.py` e `ad_remix_service.py`.

### Roteamento e fallback

Rota da operação → rota `default` → modelos habilitados em ordem de criação; filtro por capacidade;
só candidatos com credencial ativa. **Fallback automático apenas** para erros recuperáveis:
`rate_limit`, `timeout`, `unavailable`. Autenticação, permissão, cota/cobrança, requisição inválida,
modelo inexistente, conteúdo bloqueado, resposta inválida, orçamento e não configurado **param imediatamente**.

### Orçamento e custo

* Limites diário/mensal por organização; reserva atômica antes da chamada (o bloqueio de linha serializa
  requisições concorrentes), liquidação com o custo real e liberação em falha.
* Todas as janelas ou nenhuma (sem reserva parcial). Modelo sem preço **falha fechado** quando há orçamento ativo.
* Cada tentativa gera uma linha em `ai_usage_logs`: organização, usuário, provedor, modelo, operação, tokens
  (entrada, saída, cache, raciocínio), custo estimado e custo calculado, latência, tipo de erro. **Sem prompt,
  sem resposta, sem chave.**

### Migração das integrações Gemini

`copy_generation` (`copy.generate`, `copy.regenerate_field`) e `ad_remix_service` (`ad_remix.deconstruct`,
`ad_remix.reconstruct`) passam pelo gateway da organização. `GEMINI_API_KEY` global e `google-generativeai`
foram **removidos**. Mensagens de erro legadas (`Failed to parse AI response as JSON`, `Deconstruction failed`,
`Reconstruction failed`) foram preservadas.

Correção colateral: `ad_remix_prompts.RECONSTRUCTION_PROMPT` tinha chaves JSON sem escape, então
`.format()` sempre lançava `KeyError` e `/ad-remix/reconstruct` sempre devolvia 500. Escapadas apenas as duas chaves.

## 2. Endpoints (`/api/v1/ai`)

Permissões: `ai:read` (analista+), `ai:use` (gerente+, reservada para recursos/agentes futuros), `ai:manage` (admin, owner).

| Método e caminho | Permissão | Descrição |
|---|---|---|
| `GET /providers` | read | Provedores e se há credencial ativa |
| `GET /credentials` | manage | Lista (somente visão pública: `last4`, status, datas) |
| `POST /credentials` | manage | Cria (cifrada; verifica no provedor se `verify=true`) |
| `POST /credentials/{id}/test` | manage | Testa a conexão |
| `POST /credentials/{id}/rotate` | manage | Troca a chave no mesmo registro |
| `DELETE /credentials/{id}` | manage | Revoga (apaga o texto cifrado) |
| `GET /models` | read | Catálogo da organização |
| `GET /models/discover?provider=` | manage | Modelos que o provedor oferece à chave da organização |
| `POST /models`, `PATCH /models/{id}`, `DELETE /models/{id}` | manage | Gerencia o catálogo e preços |
| `GET /routing`; `PUT/DELETE /routing/{operation}` | read / manage | Modelo principal + fallbacks por operação |
| `GET /budgets`; `PUT/DELETE /budgets/{daily|monthly}` | read / manage | Limites |
| `GET /usage/summary?days=`, `GET /usage/logs` | read | Consumo por modelo, operação e usuário |

Não existe endpoint genérico de "enviar prompt": a IA roda por funcionalidades, cada uma com sua permissão.
Todos os eventos de gestão geram `audit_event` sem segredos.

## 3. Testes

| Arquivo | Cobertura |
|---|---|
| `test_ai_adapters.py` (70) | Três provedores com contratos equivalentes: texto, ferramentas, visão, JSON, uso, erros, redação, sem redirecionamento |
| `test_ai_gateway.py` (38) | Roteamento, capacidades, matriz de fallback por tipo de erro, contabilização (ex.: 1000 in × US$3/M + 500 out × US$15/M = 10 500 micros), logs sem prompt/chave, *append-only*, RLS em todas as tabelas, constraints, orçamento (liquidação, bloqueio, duas janelas, janela nova, falha fechada) e **concorrência** (24 threads disputando orçamento de 5 chamadas: exatamente 5 passam) |
| `test_ai_credentials_api.py` (26) | BYOK ponta a ponta, cifragem em repouso, chave só no cabeçalho, auditoria sem segredo, RBAC por papel, isolamento entre organizações |
| `test_ai_gateway_regression.py` (20) | Cópia e ad remix pelo gateway; chave global ignorada; mapeamento de erros sem vazamento; permissões inalteradas; orçamento → 429; fallback; ausência de configuração global/SDK e de nomes de modelo no código |
| `conftest.py` | `_no_paid_ai_calls` (autouse): qualquer tentativa de rede real falha o teste |

Execução local contra PostgreSQL descartável (`docker-compose.test.yml`, banco `*_test`).

## 4. Documentação oficial consultada e itens não confirmados

Os formatos de requisição/resposta, cabeçalhos e erros dos três provedores foram levantados na documentação
oficial vigente. Itens **não confirmados** e tratados de forma defensiva:

* valor do cabeçalho `anthropic-version` (configurável);
* cabeçalho de autenticação do `generateContent` do Gemini (usa `x-goog-api-key`);
* `role` da parte `functionResponse` no Gemini;
* parâmetro `temperature` em modelos Anthropic recentes (só enviado se definido).

## 5. Limitações

1. **Só mocks:** nenhuma homologação real com os provedores.
2. Reserva de orçamento não liberada em queda abrupta do processo permanece até a virada da janela.
3. Tokens em cache são cobrados ao preço de entrada (sem preço distinto).
4. Estimativa de entrada é heurística (`ceil(caracteres/3)+16` + máximo de saída).
5. Anthropic: modo JSON por instrução no prompt (sem recurso nativo equivalente usado).
6. API Responses da OpenAI não utilizada (Chat Completions).
7. Política de fallback não inclui cota/cobrança (a troca automática ocultaria um problema de faturamento).
8. Sem rota configurada, o roteamento é implícito (ordem de criação).
9. Frontend e i18n congelados: a matriz de papéis na UI mostra rótulos `ai:*` crus; não há tela de IA.

## 6. Pendências

* Homologação real por provedor com credenciais de teste e autorização (chamada mínima, custo controlado).
* Aplicar a migração `c9d0e1f2a3b4` em ambientes reais (não executada nesta etapa).
* Telas e agentes (fora do escopo desta etapa).
* Cadastro inicial de modelos/preços por organização (dados, não código).
