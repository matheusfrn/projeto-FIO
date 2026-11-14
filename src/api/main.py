"""
main.py — Aplicação FastAPI da plataforma Fio.

Inicializa o banco de dados no startup, registra os 4 routers e expõe
o endpoint de health check.

Uso:
    uvicorn src.api.main:app --reload
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.data.init_db import init_db
from src.api.routers import alerts, dossier, decisions, score
from src.api.routers.alerts import _RISK_RANGES
from src.api.deps import get_db
from src.models.db_models import Alert, Transaction, Customer, Dossier, AnalystDecision


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o banco na subida da aplicação."""
    init_db()
    yield


app = FastAPI(
    title="Plataforma Fio — API",
    description=(
        "API de apoio à investigação de fraudes. "
        "Gera dossiês investigativos combinando score de risco, "
        "explicabilidade SHAP e contexto do associado."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(alerts.router, tags=["alerts"])
app.include_router(dossier.router, tags=["dossier"])
app.include_router(decisions.router, tags=["decisions"])
app.include_router(score.router, tags=["score"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", summary="Health check")
def health() -> dict:
    """Retorna status da API. Usado para validar que o servidor está no ar."""
    return {"status": "ok"}


@app.get("/model/status", summary="Estado do modelo carregado")
def model_status() -> dict:
    service = score._get_scoring_service()
    return {"status": "ready", "model_version": service._model_version,
            "mock_mode": service._mock_mode}


# ---------------------------------------------------------------------------
# Stats — resumo operacional para a home do frontend
# ---------------------------------------------------------------------------
@app.get("/stats", summary="Resumo operacional")
def stats(db: Session = Depends(get_db)) -> dict:
    """Retorna contagens e agregações para a home do frontend (KPIs e gráficos)."""
    total_alerts   = db.scalar(select(func.count()).select_from(Alert)) or 0
    alerts_novo    = db.scalar(select(func.count()).select_from(Alert).where(Alert.status == "NOVO")) or 0
    alerts_enc     = db.scalar(select(func.count()).select_from(Alert).where(Alert.status == "ENCERRADO")) or 0
    total_tx       = db.scalar(select(func.count()).select_from(Transaction)) or 0
    total_customers= db.scalar(select(func.count()).select_from(Customer)) or 0
    total_dossiers = db.scalar(select(func.count()).select_from(Dossier)) or 0
    total_decisions= db.scalar(select(func.count()).select_from(AnalystDecision)) or 0

    # Distribuição de risco e valor em exposição entre os alertas pendentes (NOVO)
    risk_bucket = case(
        (Transaction.fraud_score >= _RISK_RANGES["alto"][0], "alto"),
        (Transaction.fraud_score >= _RISK_RANGES["medio"][0], "medio"),
        (Transaction.fraud_score >= _RISK_RANGES["baixo"][0], "baixo"),
        else_="indefinido",
    ).label("risk_bucket")

    risk_rows = db.execute(
        select(risk_bucket, func.count(), func.coalesce(func.sum(Transaction.amount), 0.0))
        .select_from(Alert)
        .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
        .where(Alert.status == "NOVO")
        .group_by(risk_bucket)
    ).all()

    risk_breakdown = {"baixo": 0, "medio": 0, "alto": 0, "indefinido": 0}
    pending_value_total = 0.0
    for bucket, count, amount_sum in risk_rows:
        risk_breakdown[bucket] = count
        pending_value_total += amount_sum or 0.0

    avg_score_novo = db.scalar(
        select(func.avg(Transaction.fraud_score))
        .select_from(Alert)
        .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
        .where(Alert.status == "NOVO")
    )

    # Decisões registradas, por tipo
    decision_rows = db.execute(
        select(AnalystDecision.decision, func.count()).group_by(AnalystDecision.decision)
    ).all()
    decision_breakdown = {decision: count for decision, count in decision_rows}

    # Volume de alertas por dia (toda a janela presente nos dados) — base do gráfico de tendência
    day_expr = func.substr(Transaction.event_occurred_at, 1, 10)
    daily_rows = db.execute(
        select(day_expr.label("day"), func.count().label("cnt"))
        .select_from(Alert)
        .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
        .group_by(day_expr)
        .order_by(day_expr)
    ).all()
    alerts_by_day = [{"date": row.day, "count": row.cnt} for row in daily_rows if row.day]

    return {
        "alerts_total":     total_alerts,
        "alerts_novo":      alerts_novo,
        "alerts_encerrado": alerts_enc,
        "alerts_em_analise": total_alerts - alerts_novo - alerts_enc,
        "transactions_total": total_tx,
        "customers_total":  total_customers,
        "dossiers_gerados": total_dossiers,
        "decisoes_registradas": total_decisions,
        "risk_breakdown": risk_breakdown,
        "pending_value_total": round(pending_value_total, 2),
        "avg_score_novo": round(avg_score_novo, 1) if avg_score_novo is not None else None,
        "decision_breakdown": decision_breakdown,
        "alerts_by_day": alerts_by_day,
    }
