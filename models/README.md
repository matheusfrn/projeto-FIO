# models/

Diretório para os artefatos do modelo preditivo (Artefato 6 — Tim).

**Artefatos esperados** (entregues por Tim até 04/11/2026 — contrato 13.1):

| Arquivo | Descrição |
|---------|-----------|
| `model_v1.pkl` | Rede Neural treinada (joblib/pickle) |
| `pipeline_v1.pkl` | Pipeline de pré-processamento (Scaler + Encoder + Feature Selector) |
| `explainer_v1.pkl` | SHAP KernelExplainer ou DeepExplainer |
| `feature_names.json` | Lista exata das colunas esperadas na entrada do modelo |
| `thresholds.json` | Limiares de risco: `{"baixo": 0.40, "alto": 0.70}` |
| `model_card.md` | Métricas, limitações, dataset e versão |

**Todos os arquivos `.pkl` e `.json` estão no `.gitignore`** — não versionar binários.

Enquanto os artefatos não estiverem disponíveis, o `ScoringService` opera em
**modo mock** (score `0.5`, `mock_mode: true`) conforme RN-11.
