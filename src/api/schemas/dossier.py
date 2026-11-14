"""
schemas/dossier.py — Schemas Pydantic para dossiê investigativo e desvios.

Baseado nos contratos de docs/arquitetura.md Seções 12.4 e 12.5.

RN-08: disclaimer obrigatório em todo DossierResponse.
RN-09: analyst_decision só aceita fraude_confirmada/legitima/em_analise/null.
RN-13: insufficient_history sinaliza histórico insuficiente.
RN-17: investigation_priority e investigation_trace.
RN-18: justification_text (texto livre, opcional).
RN-19: cluster_comparison_status sempre "nao_entregue_neste_ciclo".
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

_DISCLAIMER = (
    "Este dossiê é um apoio à investigação. "
    "A decisão de fraude ou legitimidade é de responsabilidade exclusiva do analista."
)

AnalystDecisionEnum = Literal["fraude_confirmada", "legitima", "em_analise"]

InvestigationPriorityEnum = Literal[
    "valor_atipico",
    "estabelecimento_historico",
    "mcc_incomum",
    "hora_incomum",
    "sem_historico_suficiente",
]


class InvestigationStep(BaseModel):
    """Um passo do rastro de investigação (RN-17, D6)."""
    step: int
    source: str
    reason: str
    result: str
    gaps: list[str] = []


class TimelineEvent(BaseModel):
    """Uma transação na linha do tempo do associado (RN-12)."""
    event_occurred_at: str
    amount: float
    acceptor_name: Optional[str] = None
    mcc: Optional[str] = None
    fraud_score: Optional[float] = None      # score externo do case


class AcceptorHistory(BaseModel):
    """Histórico do estabelecimento (N4): janela, suporte e disponibilidade de registros.

    N4: "Consultar eventos anteriores pelo identificador disponível, informar janela
    e suporte e distinguir ausência de registros de evidência de baixo risco."
    """
    window_days: int
    support: int = 0          # nº de transações observadas no estabelecimento na janela
    alerts_count: int = 0     # nº de alertas gerados para este estabelecimento na janela
    available: bool = False   # False quando não há registros suficientes (support == 0)


class DeviationReport(BaseModel):
    """Relatório de desvios comportamentais do associado (docs/arquitetura.md Seção 12.4).

    RN-05: desvio de valor calculado como (atual - média_30d) / média_30d * 100.
    RN-06: MCC incomum se não está entre os 3 mais frequentes no histórico 90d.
    RN-07: alertas relacionados nos últimos 30 dias para card_id e acceptor_id.
    RN-12: timeline com no máximo 10 transações, ordem decrescente.
    RN-13: insufficient_history=True quando < 3 transações no histórico.
    N4: acceptor_history traz janela, suporte e disponibilidade do histórico do
        estabelecimento, independente do histórico do associado estar disponível.
    """
    customer_id: str
    insufficient_history: bool = False
    acceptor_history: Optional[AcceptorHistory] = None

    # Campos de desvio — null quando insufficient_history=True (RN-13)
    avg_amount_30d: Optional[float] = None
    amount_deviation_pct: Optional[float] = None
    amount_deviation_relevant: Optional[bool] = None

    usual_mcc: Optional[str] = None
    current_mcc: Optional[str] = None
    mcc_unusual: Optional[bool] = None

    usual_hour_range: Optional[str] = None
    current_hour: Optional[str] = None
    hour_unusual: Optional[bool] = None

    related_alerts_same_card_30d: int = 0
    related_alerts_same_acceptor_30d: int = 0

    timeline: list[TimelineEvent] = []


class PriorDecision(BaseModel):
    """Uma decisão anterior já registrada para o mesmo associado, em outro alerta."""
    alert_id: str
    decision: AnalystDecisionEnum
    decided_at: str
    amount: Optional[float] = None
    event_occurred_at: Optional[str] = None


class PendingCase(BaseModel):
    """Um outro alerta ainda pendente (status NOVO) para o mesmo associado."""
    alert_id: str
    amount: Optional[float] = None
    event_occurred_at: Optional[str] = None
    fraud_score: Optional[float] = None


class RelatedCases(BaseModel):
    """Panorama de outros casos do mesmo associado — fecha a lacuna de ver um alerta
    isolado sem saber se a mesma pessoa já teve decisões anteriores ou outros casos
    abertos ao mesmo tempo (RN-05/N5: histórico e rastreabilidade entre investigações)."""
    prior_decisions: list[PriorDecision] = []
    other_pending: list[PendingCase] = []
    other_pending_count: int = 0


class DecisionRequest(BaseModel):
    """Body do POST /alerts/{alert_id}/decision (RN-09, RN-18)."""
    decision: AnalystDecisionEnum
    notes: Optional[str] = None
    justification_text: Optional[str] = None   # texto livre (D5)


class DecisionResponse(BaseModel):
    """Resposta do POST /alerts/{alert_id}/decision."""
    decision_id: str
    alert_id: str
    decision: str
    decided_at: str


class DossierResponse(BaseModel):
    """Dossiê investigativo completo retornado pela API."""

    alert_id: str
    dossier_id: str
    generated_at: str
    status: str
    model_version: Optional[str] = None

    # Seções de conteúdo (podem ser None se o dossiê ainda não foi gerado)
    transaction: Optional[dict[str, Any]] = None
    customer: Optional[dict[str, Any]] = None
    scoring: Optional[dict[str, Any]] = None
    deviations: Optional[dict[str, Any]] = None
    timeline: Optional[list[dict[str, Any]]] = None

    # Rastro de investigação (RN-17)
    investigation_priority: Optional[InvestigationPriorityEnum] = None
    investigation_trace: Optional[list[InvestigationStep]] = None

    # Panorama de outros casos do mesmo associado
    related_cases: Optional[RelatedCases] = None

    # Cluster — não entregue neste ciclo (RN-19, D4)
    cluster_comparison: None = None
    cluster_comparison_status: str = Field(default="nao_entregue_neste_ciclo")

    # Disclaimer obrigatório (RN-08)
    disclaimer: str = Field(default=_DISCLAIMER)

    # Decisão do analista (inicialmente null — RN-09, RN-18)
    analyst_decision: Optional[AnalystDecisionEnum] = None
    justification_text: Optional[str] = None
    decision_at: Optional[str] = None
