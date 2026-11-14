"""
schemas/alert.py — Schemas Pydantic para alertas e histórico de clientes.

Baseado nos contratos de docs/arquitetura.md Seção 12.2.
"""

from typing import Optional
from pydantic import BaseModel


class AlertResponse(BaseModel):
    """Representação de um alerta na API."""
    alert_id: str
    event_external_id: str
    generated_at: str
    status: str
    source: Optional[str] = None

    # Campos da transação associada (denormalizado para conveniência do frontend)
    amount: Optional[float] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    event_occurred_at: Optional[str] = None
    fraud_score: Optional[float] = None   # score externo do case (feature D1)
    risk_level: Optional[str] = None      # derivado do fraud_score externo do case

    model_config = {"from_attributes": True}


class AlertAggregates(BaseModel):
    """Agregações calculadas sobre todo o conjunto filtrado (não só a página atual)."""
    total_value: float = 0.0
    avg_score: Optional[float] = None


class AlertListResponse(BaseModel):
    """Lista paginada de alertas."""
    items: list[AlertResponse]
    total: int
    page: int
    size: int
    aggregates: AlertAggregates = AlertAggregates()


class TransactionSummary(BaseModel):
    """Resumo de uma transação para o histórico do cliente."""
    event_external_id: str
    amount: float
    event_occurred_at: str
    acceptor_id: Optional[str] = None
    mcc: Optional[str] = None
    pos_environment: Optional[str] = None
    modalidade_transacao: Optional[str] = None
    fraud_score: Optional[float] = None      # score externo do case (não o do modelo)
    transaction_status: Optional[str] = None

    model_config = {"from_attributes": True}


class CustomerHistoryResponse(BaseModel):
    """Histórico de transações de um cliente."""
    customer_id: str
    days: int
    total: int
    transactions: list[TransactionSummary]
