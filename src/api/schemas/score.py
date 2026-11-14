"""
schemas/score.py — Schemas Pydantic para scoring e explicabilidade SHAP.

Baseado no contrato de docs/arquitetura.md Seção 12.3.

Decisão D1 (29/10/2026): fraud_score, visa_fraud_score e mc_fraud_score
são features opcionais na entrada — scores externos das bandeiras incluídos
no modelo conforme acordado com Tim.

Atenção RN-16: esses campos são FEATURES de entrada (não saída do modelo).
O campo de saída é fraud_score_model para não colidir com a feature fraud_score.

Quality fields (v2 guard):
  quality_status          'complete' | 'degraded'  (default 'complete')
  available_external_scores  int ≥ 0               (default 0)
  quality_warnings        list[str]                (default [])
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

# Teto da escala dos scores externos das bandeiras no CSV do case (observado: 0–999).
EXTERNAL_SCORE_MAX = 1000.0

# Scores externos reconhecidos pelo modelo
EXTERNAL_SCORE_FIELDS = ("fraud_score", "visa_fraud_score", "mc_fraud_score")


class ScoreInput(BaseModel):
    """Payload de entrada para o endpoint POST /score."""

    event_external_id: str
    amount: float = Field(allow_inf_nan=False)
    tipo_transacao: Optional[str] = None
    modalidade_transacao: Optional[str] = None
    customer_id: Optional[str] = None
    card_id: Optional[str] = None
    acceptor_id: Optional[str] = None
    pos_environment: Optional[str] = None
    mcc: Optional[str] = None
    currency_code: Optional[str] = None
    event_occurred_at: Optional[str] = None
    device_ip_country_code: Optional[str] = None
    card_presence_flag: Optional[str] = None
    cvv2_presence_indicator: Optional[str] = None

    # Scores externos das bandeiras — features do modelo (decisão D1, RN-16)
    # Escala nativa do case: 0–999 (NÃO normalizada). Não confundir com
    # fraud_score_model (saída do modelo, em [0, 1]).
    fraud_score: Optional[float] = Field(default=None, ge=0.0, le=EXTERNAL_SCORE_MAX)
    visa_fraud_score: Optional[float] = Field(default=None, ge=0.0, le=EXTERNAL_SCORE_MAX)
    mc_fraud_score: Optional[float] = Field(default=None, ge=0.0, le=EXTERNAL_SCORE_MAX)

    model_config = {"extra": "allow", "allow_inf_nan": False}


class TopFactor(BaseModel):
    """Um fator explicativo SHAP do score."""

    feature: str
    direction: Literal["aumenta_risco", "reduz_risco", "neutro"]
    shap_value: float
    value: Any   # pode ser str, float, int, bool


class ScoreResult(BaseModel):
    """Resultado do scoring para uma transação.

    Campos de qualidade adicionados em v2 (com defaults para compatibilidade):
      quality_status            'complete' quando todos os scores externos
                                estão presentes, 'degraded' caso contrário.
      available_external_scores Quantidade de scores externos finitos
                                (fraud_score, visa_fraud_score, mc_fraud_score).
      quality_warnings          Lista de avisos textuais sobre degradação
                                de qualidade ou ausência de calibração.
    """

    event_external_id: str

    # Renomeado para não colidir com a feature fraud_score da entrada (RN-16)
    fraud_score_model: float = Field(ge=0.0, le=1.0, description="Score do modelo [0,1]")

    risk_level: Literal["baixo", "medio", "alto"]
    top_factors: list[TopFactor]
    model_version: str
    mock_mode: bool = False

    # --- Quality fields (v2) — defaults preservam compatibilidade com clientes antigos ---
    quality_status: Literal["complete", "degraded"] = "complete"
    available_external_scores: int = Field(default=0, ge=0, le=3)
    quality_warnings: list[str] = Field(default_factory=list)
    model_config = {"protected_namespaces": ()}
