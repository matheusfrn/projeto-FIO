"""
routers/alerts.py — Endpoints de alertas e histórico de clientes.

Endpoints (docs/arquitetura.md Seção 12.2):
  GET  /alerts                               → lista paginada (filtros, ordenação, agregados)
  GET  /alerts/export                        → CSV com todos os alertas filtrados (sem paginação)
  GET  /alerts/{alert_id}                    → alerta por ID ou 404
  GET  /customers/{customer_id}/history      → histórico de transações (?days=90)

Regras de negócio: nenhuma específica nesta camada (leitura pura).
"""

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.api.schemas.alert import AlertAggregates, AlertListResponse, AlertResponse, CustomerHistoryResponse, TransactionSummary
from src.api.schemas.score import EXTERNAL_SCORE_MAX
from src.models.db_models import Acceptor, Alert, Customer, Transaction

router = APIRouter()

_SORT_OPTIONS = {
    "score_desc": (Transaction.fraud_score.desc().nulls_last(), Alert.generated_at.desc()),
    "date_desc": (Transaction.event_occurred_at.desc().nulls_last(),),
    "date_asc": (Transaction.event_occurred_at.asc().nulls_last(),),
    "amount_desc": (Transaction.amount.desc().nulls_last(),),
    "amount_asc": (Transaction.amount.asc().nulls_last(),),
}


def _alert_to_response(alert: Alert, db: Session) -> AlertResponse:
    """Monta AlertResponse denormalizando campos da transação associada."""
    tx = db.get(Transaction, alert.event_external_id)
    customer_name: str | None = None
    if tx and tx.customer_id:
        customer = db.get(Customer, tx.customer_id)
        customer_name = customer.customer_name if customer else None

    # risk_level derivado do fraud_score externo do case (não do modelo).
    # O score externo está na escala 0–999; normaliza para [0, 1] antes de
    # aplicar os cortes 0.40 / 0.70 (os mesmos de RN-03).
    risk_level: str | None = None
    if tx and tx.fraud_score is not None:
        normalized = tx.fraud_score / EXTERNAL_SCORE_MAX
        if normalized < 0.40:
            risk_level = "baixo"
        elif normalized < 0.70:
            risk_level = "medio"
        else:
            risk_level = "alto"

    return AlertResponse(
        alert_id=alert.alert_id,
        event_external_id=alert.event_external_id,
        generated_at=alert.generated_at,
        status=alert.status,
        source=alert.source,
        amount=tx.amount if tx else None,
        customer_id=tx.customer_id if tx else None,
        customer_name=customer_name,
        event_occurred_at=tx.event_occurred_at if tx else None,
        fraud_score=tx.fraud_score if tx else None,
        risk_level=risk_level,
    )


_RISK_RANGES = {
    "baixo": (0.0, 0.40 * EXTERNAL_SCORE_MAX),
    "medio": (0.40 * EXTERNAL_SCORE_MAX, 0.70 * EXTERNAL_SCORE_MAX),
    "alto": (0.70 * EXTERNAL_SCORE_MAX, EXTERNAL_SCORE_MAX),
}


def _apply_alert_filters(
    stmt: Select,
    status: list[str] | None,
    risk: list[str] | None,
    date_from: str | None,
    date_to: str | None,
    amount_min: float | None,
    amount_max: float | None,
    score_min: float | None,
    score_max: float | None,
    q: str | None = None,
) -> Select:
    """Aplica os filtros de alertas a um SELECT que já faz join com Transaction.

    Decoupled da lista de colunas selecionadas de propósito: é reaplicado tanto a um
    `select(Alert)` (listagem/exportação) quanto a um `select(func.sum(...), ...)`
    (agregados), garantindo que a mesma condição WHERE valha para os dois casos.
    """
    if q:
        term = f"%{q.strip()}%"
        stmt = (
            stmt.join(Customer, Transaction.customer_id == Customer.customer_id, isouter=True)
            .join(Acceptor, Transaction.acceptor_id == Acceptor.acceptor_id, isouter=True)
            .where(or_(
                Alert.alert_id.like(term),
                Transaction.customer_id.like(term),
                Customer.customer_name.like(term),
                Acceptor.acceptor_name.like(term),
            ))
        )

    if status:
        stmt = stmt.where(Alert.status.in_([s.upper() for s in status]))

    if risk:
        risk_conditions = []
        for r in risk:
            bounds = _RISK_RANGES.get(r.lower())
            if bounds is None:
                continue
            lo, hi = bounds
            # "alto" é fechado no teto (inclui o próprio EXTERNAL_SCORE_MAX); os demais são [lo, hi)
            risk_conditions.append(
                Transaction.fraud_score >= lo if r.lower() == "alto"
                else and_(Transaction.fraud_score >= lo, Transaction.fraud_score < hi)
            )
        if risk_conditions:
            stmt = stmt.where(or_(*risk_conditions))

    if date_from:
        stmt = stmt.where(Transaction.event_occurred_at >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.event_occurred_at <= date_to)
    if amount_min is not None:
        stmt = stmt.where(Transaction.amount >= amount_min)
    if amount_max is not None:
        stmt = stmt.where(Transaction.amount <= amount_max)
    if score_min is not None:
        stmt = stmt.where(Transaction.fraud_score >= score_min)
    if score_max is not None:
        stmt = stmt.where(Transaction.fraud_score <= score_max)

    return stmt


@router.get("/alerts", response_model=AlertListResponse, summary="Listar alertas")
def list_alerts(
    status: list[str] | None = Query(
        default=None, description="Filtrar por status — repita o parâmetro para múltiplos valores (NOVO, EM_ANALISE, ENCERRADO)."
    ),
    risk: list[str] | None = Query(
        default=None, description="Filtrar por nível de risco — repita o parâmetro para múltiplos valores (baixo, medio, alto)."
    ),
    date_from: str | None = Query(default=None, description="Data/hora inicial (ISO 8601) do evento da transação."),
    date_to: str | None = Query(default=None, description="Data/hora final (ISO 8601) do evento da transação."),
    amount_min: float | None = Query(default=None, ge=0, description="Valor mínimo da transação (R$)."),
    amount_max: float | None = Query(default=None, ge=0, description="Valor máximo da transação (R$)."),
    score_min: float | None = Query(default=None, ge=0, le=EXTERNAL_SCORE_MAX, description="Score externo mínimo (escala 0–999)."),
    score_max: float | None = Query(default=None, ge=0, le=EXTERNAL_SCORE_MAX, description="Score externo máximo (escala 0–999)."),
    q: str | None = Query(default=None, description="Busca livre por ID do alerta, ID/nome do associado ou nome do estabelecimento."),
    sort: str = Query(default="score_desc", description=f"Ordenação: {', '.join(_SORT_OPTIONS)}."),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AlertListResponse:
    """Retorna lista paginada de alertas, com agregados calculados sobre todo o conjunto filtrado."""
    filters = (status, risk, date_from, date_to, amount_min, amount_max, score_min, score_max, q)

    base_join = select(Alert).join(Transaction, Alert.event_external_id == Transaction.event_external_id, isouter=True)
    base_stmt = _apply_alert_filters(base_join, *filters)

    total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0

    agg_join = select(
        func.coalesce(func.sum(Transaction.amount), 0.0),
        func.avg(Transaction.fraud_score),
    ).select_from(Alert).join(Transaction, Alert.event_external_id == Transaction.event_external_id, isouter=True)
    agg_stmt = _apply_alert_filters(agg_join, *filters)
    agg_row = db.execute(agg_stmt).one()
    aggregates = AlertAggregates(
        total_value=round(agg_row[0] or 0.0, 2),
        avg_score=round(agg_row[1], 1) if agg_row[1] is not None else None,
    )

    order_by = _SORT_OPTIONS.get(sort, _SORT_OPTIONS["score_desc"])
    stmt = base_stmt.order_by(*order_by)
    alerts = db.scalars(stmt.offset((page - 1) * size).limit(size)).all()

    return AlertListResponse(
        items=[_alert_to_response(a, db) for a in alerts],
        total=total,
        page=page,
        size=size,
        aggregates=aggregates,
    )


@router.get("/alerts/export", summary="Exportar alertas filtrados em CSV")
def export_alerts_csv(
    status: list[str] | None = Query(default=None),
    risk: list[str] | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    amount_min: float | None = Query(default=None, ge=0),
    amount_max: float | None = Query(default=None, ge=0),
    score_min: float | None = Query(default=None, ge=0, le=EXTERNAL_SCORE_MAX),
    score_max: float | None = Query(default=None, ge=0, le=EXTERNAL_SCORE_MAX),
    q: str | None = Query(default=None),
    sort: str = Query(default="score_desc"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Exporta todos os alertas que atendem aos filtros atuais em CSV, sem paginação.

    Usa os mesmos filtros de `GET /alerts`, aplicados pelo mesmo helper — garante que o
    CSV exportado corresponda exatamente ao recorte vísivel na tela (apenas sem o limite
    de página).
    """
    base_join = select(Alert).join(Transaction, Alert.event_external_id == Transaction.event_external_id, isouter=True)
    base_stmt = _apply_alert_filters(
        base_join, status, risk, date_from, date_to, amount_min, amount_max, score_min, score_max, q
    )
    order_by = _SORT_OPTIONS.get(sort, _SORT_OPTIONS["score_desc"])
    alerts = db.scalars(base_stmt.order_by(*order_by)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "alert_id", "status", "risco", "score_externo", "valor_brl",
        "data_hora_evento", "customer_id", "customer_name",
    ])
    for alert in alerts:
        item = _alert_to_response(alert, db)
        writer.writerow([
            item.alert_id, item.status, item.risk_level or "", item.fraud_score or "",
            item.amount or "", item.event_occurred_at or "", item.customer_id or "", item.customer_name or "",
        ])

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alertas_fio.csv"},
    )


@router.get("/alerts/{alert_id}", response_model=AlertResponse, summary="Buscar alerta por ID")
def get_alert(alert_id: str, db: Session = Depends(get_db)) -> AlertResponse:
    """Retorna um alerta pelo ID ou 404."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alerta '{alert_id}' não encontrado.")
    return _alert_to_response(alert, db)


@router.get(
    "/customers/{customer_id}/history",
    response_model=CustomerHistoryResponse,
    summary="Histórico de transações do cliente",
)
def get_customer_history(
    customer_id: str,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
) -> CustomerHistoryResponse:
    """Retorna as transações do cliente nos últimos N dias, em ordem decrescente.

    O cutoff é calculado em relação à transação mais recente do cliente (não à
    data atual do servidor), garantindo que dados históricos do case sintético
    de 2025 sejam visíveis mesmo com o servidor rodando em 2026.
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"Cliente '{customer_id}' não encontrado.")

    # Referência temporal: data da transação mais recente do cliente
    most_recent = db.scalar(
        select(Transaction.event_occurred_at)
        .where(Transaction.customer_id == customer_id)
        .order_by(Transaction.event_occurred_at.desc())
        .limit(1)
    )
    if most_recent:
        try:
            ref_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
        except Exception:
            ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = datetime.now(timezone.utc)

    cutoff = (ref_dt - timedelta(days=days)).isoformat()
    stmt = (
        select(Transaction)
        .where(Transaction.customer_id == customer_id)
        .where(Transaction.event_occurred_at >= cutoff)
        .order_by(Transaction.event_occurred_at.desc())
    )
    transactions = db.scalars(stmt).all()

    return CustomerHistoryResponse(
        customer_id=customer_id,
        days=days,
        total=len(transactions),
        transactions=[
            TransactionSummary(
                event_external_id=tx.event_external_id,
                amount=tx.amount,
                event_occurred_at=tx.event_occurred_at,
                acceptor_id=tx.acceptor_id,
                mcc=tx.mcc,
                pos_environment=tx.pos_environment,
                modalidade_transacao=tx.modalidade_transacao,
                fraud_score=tx.fraud_score,
                transaction_status=tx.transaction_status,
            )
            for tx in transactions
        ],
    )
