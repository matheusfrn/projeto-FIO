# Plano de Implementação — Plataforma Fio

**Baseado em:** `docs/arquitetura.md` (Artefato 8 — Versão 2.0)
**Objetivo:** Implementar o MVP da plataforma Fio conforme a arquitetura definida.
**Responsáveis:** Thi (implementação), Tim (modelo + explicabilidade), Isa (dados)

> **Antes de iniciar qualquer sub-tarefa:** leia `docs/arquitetura.md` na íntegra para obter contexto completo de componentes, contratos, regras de negócio (RN-01 a RN-16) e diagramas.

---

## Visão Geral da Arquitetura (resumo)

A plataforma Fio é composta por **6 componentes** distribuídos em **3 camadas**:

```
Camada de Apresentação  →  Frontend (Streamlit / React)
Camada de Serviços      →  Backend API (FastAPI)
                              ├── Alert Router
                              ├── Dossier Router
                              ├── Decision Router
                              └── Score Router
                                  ├── Scoring Service (modelo + SHAP)
                                  ├── Evidence Aggregator (histórico + desvios)
                                  └── Dossier Builder (monta + persiste)
Camada de Dados         →  SQLite (transactions, alerts, dossiers, analyst_decisions)
                         →  Artefatos do modelo (models/*.pkl, models/*.json)
```

**Máquina de estados do Dossiê:** PENDENTE → GERANDO → PRONTO → EM_ANALISE → CONCLUIDO / AGUARDANDO_INFO

**Regras invioláveis:** RN-08 (disclaimer obrigatório), RN-16 (sem leakage no modelo), RN-09 (decisão só pelo analista).

---

## Ordem de Execução e Dependências

```
ST-1  Infraestrutura Base
  └──> ST-2  Carga dos Dados (independente de ST-3)
  └──> ST-3  Scoring Service (independente de ST-2)
              ST-2 + ST-3 ──> ST-4  Evidence Aggregator + Dossier Builder
                                └──> ST-5  Endpoints da API
                                           └──> ST-6  Frontend MVP
```

---

## ST-1 — Infraestrutura Base

**Status:** `[ ] pending`

**Intent:** Criar a estrutura de arquivos, dependências e configuração para que todas as outras sub-tarefas iniciem sem conflito de ambiente.

**Expected Outcomes:**
- Estrutura de diretórios conforme `docs/arquitetura.md` Seção 7.
- `requirements.txt` com todas as dependências listadas.
- Aplicação FastAPI sobe sem erros com rota `GET /health`.
- Banco SQLite inicializado com o schema completo (7 tabelas).

**Todo List:**
1. Criar diretórios: `src/api/routers/`, `src/api/schemas/`, `src/services/`, `src/models/`, `src/frontend/`, `src/data/`, `data/raw/`, `data/processed/`, `models/`, `notebooks/`.
2. Criar `requirements.txt` com: `fastapi`, `uvicorn`, `sqlalchemy`, `pandas`, `numpy`, `scikit-learn`, `shap`, `pydantic`, `streamlit`, `joblib`, `python-dotenv`.
3. Adicionar `data/raw/` ao `.gitignore`.
4. Criar `src/api/main.py` com FastAPI app, `GET /health` retornando `{"status": "ok"}` e registro dos 4 routers.
5. Criar `src/models/db_models.py` com SQLAlchemy ORM para as tabelas: `transactions`, `customers`, `cards`, `acceptors`, `alerts`, `dossiers`, `analyst_decisions` — conforme schema da Seção 12.7 e ER da Seção 6 do `arquitetura.md`.
6. Criar `src/data/init_db.py` que cria todas as tabelas via `Base.metadata.create_all`.
7. Validar: `uvicorn src.api.main:app --reload` sobe e `GET /health` retorna 200.

**Regras de negócio relevantes:** —

**Relevant Context:**
- Schema completo das tabelas em `docs/arquitetura.md` Seção 12.7 e Seção 6 (ER Diagram).
- Stack: FastAPI + SQLAlchemy + SQLite.
- Diagrama de componentes: `docs/arquitetura.md` Seção 3.3.

---

## ST-2 — Carga dos Dados do Case

**Status:** `[ ] pending`
**Depende de:** ST-1

**Intent:** Importar a base de dados do case para o banco local, normalizando as entidades (Customer, Card, Acceptor, Transaction, Alert) e tornando-as consultáveis pela API.

**Expected Outcomes:**
- Tabelas `transactions`, `customers`, `cards`, `acceptors` populadas com os dados do case.
- Tabela `alerts` populada com os registros suspeitos. A EDA confirmou que `fraud_score` é variável contínua com forte discriminação (~4,4× razão de separação); usar percentil 95 de `fraud_score` como limiar inicial — calibrar com Tim após o modelo estar disponível.
- Script reproduzível e documentado.

**Todo List:**
1. Colocar o CSV do case em `data/raw/` (não versionar).
2. Implementar `src/data/import_transactions.py`:
   a. Ler o CSV com Pandas, inferir tipos conforme dicionário de dados.
   b. Desduplicar e normalizar entidades para tabelas separadas (`customers`, `cards`, `acceptors`).
   c. Inserir registros na tabela `transactions` com campo `raw_json` contendo a linha original.
   d. Excluir ou nunca usar para scoring os campos: `feedback_label`, `dispute_*`, `fraud`, `decision` — **RN-16**.
3. Gerar registros em `alerts` para transações com `fraud_score` acima do percentil 95 (ou limiar acordado com Tim após o modelo); status inicial: `NOVO`. **Nota da EDA:** `fraud_score` tem forte poder discriminante; `decision=deny` e `transaction_status=declined` são leakage pós-evento e não devem ser usados como critério de alerta.
4. Validar contagem de registros importados e verificar campos obrigatórios (`customer_id`, `amount`, `event_occurred_at`).
5. Documentar o comando de execução no `README.md`.

**Regras de negócio relevantes:** RN-16 (campos de resultado posterior excluídos da predição).

**Relevant Context:**
- Dicionário de dados completo no prompt de arquitetura (297 colunas, 8 categorias).
- Schema das tabelas em `docs/arquitetura.md` Seção 12.7.
- Diagrama ER em `docs/arquitetura.md` Seção 6.

---

## ST-3 — Scoring Service

**Status:** `[ ] pending`
**Depende de:** ST-1 (infraestrutura)
**Em paralelo com:** ST-2
**Aguarda (para modo real):** modelo serializado de Tim (`models/model_v1.pkl`, `models/pipeline_v1.pkl`, `models/explainer_v1.pkl`, `models/feature_names.json`, `models/thresholds.json`)

**Intent:** Implementar o serviço que carrega o modelo treinado e gera score + explicações SHAP para uma transação. Deve funcionar em modo mock antes do modelo real estar disponível (RN-11). O modelo **inclui** `fraud_score`, `visa_fraud_score` e `mc_fraud_score` como features (decisão D1).

**Expected Outcomes:**
- `src/services/scoring_service.py` funcional com modo real e modo mock.
- Endpoint `POST /score` operacional e validado com payload de exemplo.
- Contrato de saída com `fraud_score`, `risk_level`, `top_factors` (com `shap_value` explícito) e `mock_mode`.
- `ScoreInput` inclui os campos `fraud_score`, `visa_fraud_score`, `mc_fraud_score` como features opcionais (presentes nos dados do case).

**Todo List:**
1. Criar schemas Pydantic em `src/api/schemas/score.py`:
   - `ScoreInput` com campos mínimos (ver contrato Seção 12.3 do `arquitetura.md`); incluir `fraud_score`, `visa_fraud_score`, `mc_fraud_score` como campos opcionais — **D1**.
   - `TopFactor` com campos: `feature`, `direction`, `shap_value`, `value`.
   - `ScoreResult` com: `event_external_id`, `fraud_score_model` (renomear para não colidir com feature), `risk_level`, `top_factors`, `model_version`, `mock_mode`.
2. Implementar `src/services/scoring_service.py` com classe `ScoringService`:
   - `load_artifacts(models_dir)`: carrega `model_v1.pkl`, `pipeline_v1.pkl`, `explainer_v1.pkl`, `feature_names.json`, `thresholds.json`.
   - `predict(transaction_dict) -> ScoreResult`: aplica pipeline, roda modelo, gera SHAP top-5 ordenados por `abs(shap_value)`.
   - Garantir que `fraud_score`, `visa_fraud_score`, `mc_fraud_score` são tratados como features de entrada, não como saída — **RN-16 (D1)**.
   - Classificar `risk_level` usando limiares de `thresholds.json` — **RN-03**.
   - Preencher features ausentes com mediana/moda do pipeline — **RN-10**.
   - Se artefato não existir: retornar score `0.5`, `risk_level = "medio"`, `mock_mode = true`, log de aviso — **RN-11**.
3. Classificar `direction` de cada fator SHAP: `aumenta_risco` se `shap_value > 0`, `reduz_risco` se `< 0`, `neutro` se `abs < 0.01` — **RN-04**.
4. Criar `src/api/routers/score.py` com `POST /score` que instancia `ScoringService` e retorna `ScoreResult`.
5. Registrar o router em `src/api/main.py`.
6. Testar com payload do exemplo da Seção 12.3 do `arquitetura.md` — verificar que retorna estrutura correta em mock.
7. **Quando Tim entregar o modelo:** substituir mock, validar que score de saída é float [0,1] e diferente das features de entrada `fraud_score`/`visa_fraud_score`/`mc_fraud_score` — **RN-02**.

**Regras de negócio relevantes:** RN-02, RN-03, RN-04, RN-10, RN-11, RN-16.

**Relevant Context:**
- Contrato de entrada e saída completo em `docs/arquitetura.md` Seção 12.3.
- Diagrama de atividade do processamento do score em `docs/arquitetura.md` Seção 8.1.
- Artefatos do modelo descritos em `docs/arquitetura.md` Seção 12.6.

---

## ST-4 — Evidence Aggregator e Dossier Builder

**Status:** `[ ] pending`
**Depende de:** ST-2 (dados na base), ST-3 (scoring service)

**Intent:** Implementar a lógica que agrega evidências (histórico, desvios, eventos relacionados) e o Dossier Builder que combina score + evidências, identifica o indício dominante (`investigation_priority`), gera o rastro de investigação (`investigation_trace`) e persiste o dossiê completo (RN-01, RN-14, RN-17, RN-18, RN-19).

**Expected Outcomes:**
- `src/services/evidence_aggregator.py` retorna `DeviationReport` com todos os campos da Seção 12.4.
- `src/services/dossier_builder.py` monta e persiste dossiê com `investigation_priority`, `investigation_trace`, `cluster_comparison_status = "nao_entregue_neste_ciclo"` e `justification_text = null`.
- Dossiê salvo com `disclaimer` obrigatório, `analyst_decision = null`, `justification_text = null` — **RN-08, RN-09, RN-18**.
- Máquina de estados do dossiê respeitada (estados: `pendente → gerando → pronto`).

**Todo List:**
1. Implementar `src/services/evidence_aggregator.py`:
   - `aggregate(customer_id, card_id, acceptor_id, event_occurred_at) -> DeviationReport`.
   - Buscar transações do `customer_id` dos últimos 90 dias.
   - Verificar histórico mínimo: se `< 3` registros, retornar `insufficient_history: true` com campos de desvio `null` — **RN-13**.
   - Calcular `avg_amount_30d` e `amount_deviation_pct = (atual - média) / média * 100` — **RN-05**.
   - `amount_deviation_relevant = True` se desvio `> 50%`.
   - Identificar `usual_mcc` como o mais frequente nos últimos 90 dias; `mcc_unusual = True` se MCC atual não está entre os 3 mais frequentes — **RN-06**.
   - Calcular faixa horária habitual e `hour_unusual`.
   - Contar alertas relacionados: `card_id` e `acceptor_id` nos últimos 30 dias — **RN-07**.
   - Montar `timeline`: últimas 10 transações, ordem decrescente — **RN-12**.
2. Criar schema Pydantic `DeviationReport` em `src/api/schemas/dossier.py`.
3. Implementar `src/services/dossier_builder.py`:
   - `build(alert_id) -> DossierResponse`.
   - Verificar se dossiê já existe na tabela `dossiers` — **RN-14** (cache por persistência).
   - Atualizar status do dossiê para `GERANDO` antes de iniciar.
   - Chamar `ScoringService.predict()` e `EvidenceAggregator.aggregate()`.
   - **Calcular `investigation_priority`** com a seguinte lógica de prioridade (D3 — RN-17):
     - Se `insufficient_history = True` → `"sem_historico_suficiente"`
     - Senão se `amount_deviation_relevant = True` → `"valor_atipico"`
     - Senão se `mcc_unusual = True` → `"mcc_incomum"`
     - Senão se `hour_unusual = True` → `"hora_incomum"`
     - Senão se `related_alerts_same_acceptor_30d > 0` → `"estabelecimento_historico"`
     - Senão → `"valor_atipico"` (fallback)
   - **Construir `investigation_trace`** (D6 — RN-17): lista de passos com `{step, source, reason, result, gaps}` registrando cada fonte consultada e o que foi encontrado.
   - Montar estrutura JSON completa conforme Seção 12.5 do `arquitetura.md`.
   - Garantir que `disclaimer` é sempre o texto fixo definido — **RN-08**.
   - Incluir `cluster_comparison_status = "nao_entregue_neste_ciclo"` e `cluster_comparison = null` — **RN-19** (D4).
   - Garantir que `analyst_decision = null`, `justification_text = null` na criação — **RN-09, RN-18**.
   - Persistir dossiê na tabela `dossiers` com status `PRONTO`.
   - Incluir `model_version` do `ScoreResult` — **RN-15**.
4. Criar schema `DossierResponse` completo em `src/api/schemas/dossier.py`, incluindo `investigation_priority` (str), `investigation_trace` (list), `cluster_comparison` (null), `cluster_comparison_status` (str), `justification_text` (str | null).
5. Testar com um `alert_id` real importado via ST-2.
6. Validar que o `disclaimer` está presente, o `investigation_priority` é um dos 5 valores válidos (RN-17), e o `cluster_comparison_status` é sempre `"nao_entregue_neste_ciclo"` (RN-19).

**Regras de negócio relevantes:** RN-01, RN-05, RN-06, RN-07, RN-08, RN-09, RN-12, RN-13, RN-14, RN-15, RN-17, RN-18, RN-19.

**Relevant Context:**
- Contrato de saída do Evidence Aggregator em `docs/arquitetura.md` Seção 12.4.
- Contrato de saída do Dossier Builder em `docs/arquitetura.md` Seção 12.5.
- Diagrama de atividade dos desvios em `docs/arquitetura.md` Seção 8.2.
- Diagrama de atividade do ciclo de vida do alerta em `docs/arquitetura.md` Seção 8.3.
- Máquina de estados do dossiê em `docs/arquitetura.md` Seção 9.

---

## ST-5 — Endpoints da API

**Status:** `[ ] pending`
**Depende de:** ST-4

**Intent:** Expor todos os endpoints REST da tabela da Seção 12.2, conectando routers ao Dossier Builder, banco de dados e máquina de estados do dossiê. Implementar lógica de cache, tratamento de erros e validação dos códigos HTTP.

**Expected Outcomes:**
- Todos os endpoints listados na Seção 12.2 do `arquitetura.md` respondem com os contratos corretos.
- `GET /alerts/{alert_id}/dossier` retorna `202` se dossiê está em estado `GERANDO`.
- `POST /alerts/{alert_id}/decision` valida o enum de `analyst_decision` e atualiza status do alerta.
- Erros de schema retornam `422` com detalhes do campo.
- Fluxos de sequência da Seção 7 do `arquitetura.md` implementados.

**Todo List:**
1. Criar `src/api/routers/alerts.py`:
   - `GET /alerts` com filtro opcional `?status=` e paginação `?page=&size=`.
   - `GET /alerts/{alert_id}` retornando `AlertResponse` ou `404`.
   - `GET /customers/{customer_id}/history?days=90` retornando `CustomerHistoryResponse`.
2. Criar `src/api/routers/dossier.py`:
   - `GET /alerts/{alert_id}/dossier`:
     - Verificar cache (tabela `dossiers`).
     - Se estado `GERANDO`: retornar `HTTP 202`.
     - Se não existe: acionar `DossierBuilder.build()` e retornar `200`.
     - Se existe e `PRONTO`: retornar dossiê diretamente.
3. Criar `src/api/routers/decisions.py`:
   - `POST /alerts/{alert_id}/decision`:
     - Validar que `decision` é um dos valores permitidos: `fraude_confirmada`, `legitima`, `em_analise` — **RN-09**.
     - Aceitar `justification_text` (TEXT, opcional) no body — **RN-18 (D5)**.
     - `INSERT INTO analyst_decisions` com `decision`, `notes`, `justification_text`, `decided_at`.
     - `UPDATE dossiers SET analyst_decision = ..., justification_text = ..., status = concluido`.
     - `UPDATE alerts SET status = encerrado`.
     - Retornar `201 Created`.
     - Se alerta não existe: `404`.
4. Registrar todos os routers em `src/api/main.py`.
5. Testar todos os endpoints com curl ou httpie:
   - Fluxo completo: GET alerta → GET dossiê → POST decisão.
   - Caso de erro: GET alerta inexistente → 404.
   - Caso de validação: POST decisão com valor inválido → 422.
6. Documentar exemplos de request/response no `README.md`.

**Regras de negócio relevantes:** RN-01, RN-08, RN-09, RN-14.

**Relevant Context:**
- Tabela de endpoints em `docs/arquitetura.md` Seção 12.2.
- Diagramas de sequência dos 3 fluxos em `docs/arquitetura.md` Seção 7.
- Máquina de estados em `docs/arquitetura.md` Seção 9.

---

## ST-6 — Frontend MVP

**Status:** `[ ] pending`
**Depende de:** ST-5

**Intent:** Criar a interface do analista que exibe a lista de alertas e o dossiê investigativo completo, consumindo a API, respeitando as regras de interface definidas (disclaimer sempre visível, badge de cor, sinalização de mock).

**Expected Outcomes:**
- Tela **Lista de Alertas** navegável com score e badge de nível de risco visíveis.
- Tela **Dossiê** com todas as 9 seções: Transação, Score, Fatores, Contexto do Associado, Desvios, Linha do Tempo, Prioridade de Investigação, Disclaimer, Decisão.
- Disclaimer sempre visível sem necessidade de scroll — **RN-08**.
- Badge de cor: verde (baixo), amarelo (médio), vermelho (alto) — **RN-03**.
- Campos mock com label `[SIMULADO]`.
- Formulário de decisão com os 3 valores permitidos — **RN-09**.
- Interface acessível em `localhost:3000` (Streamlit) ou porta configurada.

**Todo List:**
1. **Decisão de stack:** registrar em `docs/planejamento.md` Seção 10 se será Streamlit ou React. Recomendação: Streamlit para MVP de demonstração (menor esforço para Thi).
2. Implementar tela **Lista de Alertas**:
   - Consumir `GET /alerts`.
   - Tabela com colunas: `alert_id`, `customer_name`, `amount`, `event_occurred_at`, badge de `risk_level`, `status`.
   - Ao clicar na linha, navegar para o Dossiê.
3. Implementar tela **Dossiê Investigativo**:
   - Consumir `GET /alerts/{alert_id}/dossier`.
   - **Seção Disclaimer:** banner fixo no topo com texto de RN-08. Nunca oculto.
   - **Seção Transação:** campos de `transaction` do dossiê.
   - **Seção Score de Risco:** gauge ou barra com `fraud_score` e badge colorido de `risk_level`.
   - **Seção Fatores Relevantes:** lista de `top_factors` com direção (ícone ↑/↓) e valor de `shap_value`. Se `mock_mode = true`, exibir `[SIMULADO]`.
   - **Seção Contexto do Associado:** campos de `customer` do dossiê.
   - **Seção Desvios:** tabela com `amount_deviation_pct`, `mcc_unusual`, `hour_unusual`, `related_alerts_same_card_30d`. Destacar visualmente os `true`.
   - **Seção Linha do Tempo:** lista cronológica de `timeline`, do mais recente ao mais antigo.
   - **Seção Prioridade de Investigação (D3):** exibir `investigation_priority` como banner/destaque indicando o indício dominante do caso. Exibir `investigation_trace` como lista colapsável "Como este dossiê foi construído". Incluir campo `cluster_comparison_status` com label "Comparação com perfis similares: não disponível neste ciclo" — **RN-19**.
   - **Seção Decisão:** dropdown com `fraude_confirmada` / `legitima` / `em_analise` + campo de notas + campo de **justificativa** (texto livre, opcional) + botão confirmar — **RN-18 (D5)**. Desabilitar se já houver `analyst_decision`.
4. Consumir `POST /alerts/{alert_id}/decision` ao confirmar decisão.
5. Testar fluxo completo de ponta a ponta com um caso real da base.
6. Verificar que todos os campos de mock têm label `[SIMULADO]` e que o disclaimer é visível sem scroll.
7. Documentar dados simulados ainda presentes na demonstração.

**Regras de negócio relevantes:** RN-03, RN-08, RN-09, RN-11.

**Relevant Context:**
- Telas e regras de interface em `docs/arquitetura.md` Seção 12.1.
- Diagrama de casos de uso em `docs/arquitetura.md` Seção 4.
- Critério de conclusão do Artefato 9 em `docs/planejamento.md` Seção 3.2.

---

## Checklist Global de Aceite

### Infraestrutura e Dados
- [ ] ST-1: banco SQLite com 7 tabelas inicializado.
- [ ] ST-2: base do case importada, entidades normalizadas, alertas gerados.

### Serviços de IA
- [ ] ST-3: scoring service retorna score + top_factors com `shap_value` (mock ou real).
- [ ] ST-3: modo mock identificado com `mock_mode: true`.
- [ ] ST-3: `ScoreInput` aceita `fraud_score`, `visa_fraud_score`, `mc_fraud_score` como features opcionais (D1).
- [ ] ST-3: após integração com Tim — score de saída (`fraud_score_model`) é float [0,1] e não colide com as features de entrada (RN-02, D1).

### Lógica de Negócio
- [ ] ST-4: desvios calculados com regras RN-05, RN-06, RN-07.
- [ ] ST-4: histórico insuficiente retorna `insufficient_history: true` (RN-13).
- [ ] ST-4: `disclaimer` presente e imutável em todos os dossiês (RN-08).
- [ ] ST-4: `analyst_decision = null` e `justification_text = null` na criação do dossiê (RN-09, RN-18).
- [ ] ST-4: campo `investigation_priority` calculado com a lógica de prioridade definida (RN-17, D3).
- [ ] ST-4: campo `investigation_trace` gerado com rastro das etapas (RN-17, D6).
- [ ] ST-4: `cluster_comparison_status = "nao_entregue_neste_ciclo"` em todo dossiê gerado (RN-19, D4).
- [ ] ST-4: cache de dossiê funcionando (RN-14).

### API
- [ ] ST-5: todos os 7 endpoints respondem conforme tabela da Seção 12.2.
- [ ] ST-5: `GET /dossier` retorna `202` quando dossiê está sendo gerado.
- [ ] ST-5: `POST /decision` aceita `justification_text` opcional e valida enum (RN-09, RN-18, D5).
- [ ] ST-5: `POST /decision` retorna `422` para valores de `decision` inválidos (RN-09).

### Frontend
- [ ] ST-6: disclaimer visível sem scroll em todas as visualizações de dossiê (RN-08).
- [ ] ST-6: badge de cor por nível de risco (RN-03).
- [ ] ST-6: campos mock sinalizados com `[SIMULADO]`.
- [ ] ST-6: seção "Prioridade de Investigação" exibe `investigation_priority` e `investigation_trace` (D3).
- [ ] ST-6: label "Comparação com perfis similares: não disponível neste ciclo" visível no dossiê (D4, RN-19).
- [ ] ST-6: campo de justificativa livre no formulário de decisão (D5, RN-18).
- [ ] ST-6: formulário de decisão com os 3 valores corretos (RN-09).

### Fluxo Completo
- [ ] Fluxo de ponta a ponta demonstrável: alerta → dossiê → decisão com justificativa.
- [ ] Nenhum campo de resultado posterior alimenta o modelo (RN-16); scores externos usados como features estão documentados no model card (D1).
- [ ] Sistema não toma decisões automáticas de bloqueio ou confirmação de fraude.
