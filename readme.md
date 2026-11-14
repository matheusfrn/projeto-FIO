# Plataforma Fio — MVP

Sistema de apoio à investigação de fraudes para analistas do Sicredi.

> **Artefato 9 — Plataforma de apoio à investigação (MVP)**  
> Responsável: Thi (implementação) | Prazo: 06/11/2026

---

## Execução rápida

### 1. Pré-requisitos

```bash
pip install -r requirements.txt
```

### 2. Preparar os dados

Coloque o CSV do case em `data/raw/case_transacoes_cartoes_sintetico_900k.csv` e execute a carga:

```bash
python -m src.data.import_transactions
```

> O banco SQLite (`data/fio.db`) é criado automaticamente na primeira execução da API.

### 3. Subir a API (backend)

```bash
uvicorn src.api.main:app --reload
# API disponível em http://localhost:8000
# Documentação interativa: http://localhost:8000/docs
```

### 4. Subir o frontend

```bash
streamlit run src/frontend/app.py
# Interface disponível em http://localhost:8501
```

---

## Estrutura do projeto

```
src/
├── api/
│   ├── main.py              # FastAPI app + health check
│   ├── deps.py              # Session SQLAlchemy (DI)
│   ├── routers/             # alerts, dossier, decisions, score
│   └── schemas/             # Pydantic: alert, dossier, score
├── services/
│   ├── scoring_service.py   # Score + SHAP (modo mock ou real)
│   ├── evidence_aggregator.py  # Desvios + timeline
│   └── dossier_builder.py   # Orquestra dossiê completo
├── models/
│   └── db_models.py         # SQLAlchemy ORM (7 tabelas)
├── data/
│   └── init_db.py           # Inicializa banco
└── frontend/
    └── app.py               # Interface Streamlit

data/
├── raw/          # CSV do case (não versionado)
└── processed/    # Dados preparados por Isa

models/           # Artefatos do modelo de Tim (não versionados)
docs/             # Documentação do projeto
notebooks/        # EDA e modelagem (Isa/Tim)
```

---

## Artefatos do modelo (Matheus → plataforma)

Coloque em `models/` os cinco arquivos abaixo. Os nomes continuam os mesmos por
compatibilidade com o `ScoringService`, mas a versão lógica atual é **v2.1.0**
(rede neural + calibração + guards de qualidade — ver docs abaixo):

| Arquivo | Descrição |
|---------|-----------|
| `model_v1.pkl` | Modelo treinado (CalibratedClassifierCV) |
| `pipeline_v1.pkl` | Pipeline de pré-processamento |
| `explainer_v1.pkl` | SHAP explainer |
| `feature_names.json` | Lista de features — já versionado em `models/` |
| `thresholds.json` | Limiares de risco — já versionado em `models/` |

Os `.pkl` **não são versionados** (ver `.gitignore`) — são entregues separadamente
em `models/model_artifacts_v2.zip`, cuja integridade é conferida via
`models/model_artifacts_v2.zip.sha256` e `models/artifact_manifest.json`.

A partir da integração do modelo v2, o `ScoringService` roda **fail-closed por
padrão** (`allow_mock=False`): sem os `.pkl` presentes e íntegros, ou sem score
externo suficiente na entrada, o serviço recusa a geração do score (HTTP
422/503) em vez de simular silenciosamente. O modo mock antigo (`mock_mode:
true`, score = 0.5) só é ativado explicitamente com `allow_mock=True`.

Documentação do modelo:

- [Executar a aplicação localmente](docs/execucao-local.md)
- [Entrega e verificação da issue #1](docs/entrega-issue-1.md)
- [Model card, resultados e limitações](models/model_card.md)
- Pacote local `models/model_artifacts_v2.zip`: alternativa de entrega separada.
- [Treinamento e implantação](docs/treinamento-modelo.md)
- [Recusa e qualidade de entrada (guards fail-closed)](docs/scoring-quality.md)
- [Protocolo v2.1](docs/protocolo-modelo-v2.1.json)
- [Auditoria da v1](docs/auditoria-modelo-v1.md)

Os arquivos continuam chamados model_v1.pkl, pipeline_v1.pkl, explainer_v1.pkl,
feature_names.json e thresholds.json para compatibilidade. A versão lógica é
v2.1.0. Os cinco arquivos ativam o scoring no serviço original sem mudança de
código. Para as proteções adicionais desta branch, o pacote inclui manifesto
de integridade, patch de integração e instruções. Os `.pkl` da versão ativa
em `models/` devem ser incluídos no commit. O ZIP continua sendo uma entrega
separada e opcional.

Os resultados são internos ao case sintético. Calibração preliminar e teste
histórico já inspecionado não constituem validação bancária em produção
(`thresholds.json` registra `business_approved: false`).

---

## Endpoints da API

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/health` | Health check |
| `GET` | `/alerts` | Lista paginada de alertas |
| `GET` | `/alerts/{id}` | Alerta por ID |
| `GET` | `/alerts/{id}/dossier` | Dossiê investigativo |
| `POST` | `/alerts/{id}/decision` | Registrar decisão do analista |
| `GET` | `/customers/{id}/history` | Histórico de transações |
| `POST` | `/score` | Score direto para uma transação |

Documentação completa: `http://localhost:8000/docs`

---

## Dados simulados

- Score e fatores SHAP operam em **modo mock** apenas quando o `ScoringService` é
  explicitamente construído com `allow_mock=True` (compatibilidade retroativa).
  Por padrão, sem os `.pkl` íntegros em `models/`, o serviço recusa o score
  (fail-closed) em vez de simular — ver [recusa e qualidade de entrada](docs/scoring-quality.md).
- Campos em modo mock são sinalizados com `[SIMULADO]` na interface.
- A base do case é **sintética** — os padrões observados não refletem a operação real do Sicredi.

> **RN-08:** O sistema exibe sempre o disclaimer: *"Este dossiê é um apoio à investigação. A decisão de fraude ou legitimidade é de responsabilidade exclusiva do analista."*
