"""
routers/decisions.py — Endpoint de registro de decisão do analista.

POST /alerts/{alert_id}/decision:
  - 201 Created  → decisão registrada com sucesso
  - 404          → alerta não encontrado
  - 422          → valor de decision inválido (validado pelo Pydantic)

Regras de negócio:
  RN-09  decision: fraude_confirmada / legitima / em_analise
  RN-18  justification_text: texto livre, opcional (decisão D5)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.api.schemas.dossier import DecisionRequest, DecisionResponse
from src.models.db_models import Alert, AnalystDecision, Dossier

router = APIRouter()

_DECISION_TO_DOSSIER_STATUS = {
    "fraude_confirmada": "concluido_fraude",
    "legitima": "concluido_legitimo",
    "em_analise": "aguardando_info",
}


@router.post(
    "/alerts/{alert_id}/decision",
    response_model=DecisionResponse,
    status_code=201,
    summary="Registrar decisão do analista",
)
def register_decision(
    alert_id: str,
    body: DecisionRequest,
    db: Session = Depends(get_db),
) -> DecisionResponse:
    """Registra a decisão humana sobre um alerta.

    Persiste em analyst_decisions, atualiza dossiers e fecha o alerta.
    O valor de `decision` é validado pelo Pydantic — valores inválidos retornam 422.

    Fluxo (docs/arquitetura.md Seção 7.2):
      INSERT INTO analyst_decisions
      UPDATE dossiers SET analyst_decision, justification_text, status, decision_at
      UPDATE alerts SET status = encerrado
    """
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alerta '{alert_id}' não encontrado.")

    now = datetime.now(timezone.utc).isoformat()
    decision_id = f"DEC_{uuid.uuid4().hex[:12].upper()}"

    # INSERT analyst_decisions
    record = AnalystDecision(
        decision_id=decision_id,
        alert_id=alert_id,
        decision=body.decision,
        notes=body.notes,
        justification_text=body.justification_text,
        decided_at=now,
    )
    db.add(record)

    # UPDATE dossiers — se existir (RN-09, RN-18)
    dossier = db.query(Dossier).filter(Dossier.alert_id == alert_id).first()
    if dossier:
        dossier.analyst_decision = body.decision
        dossier.justification_text = body.justification_text
        dossier.decision_at = now
        dossier.status = _DECISION_TO_DOSSIER_STATUS.get(body.decision, "concluido_legitimo")

    # UPDATE alerts SET status = encerrado
    alert.status = "ENCERRADO"

    db.commit()

    return DecisionResponse(
        decision_id=decision_id,
        alert_id=alert_id,
        decision=body.decision,
        decided_at=now,
    )
