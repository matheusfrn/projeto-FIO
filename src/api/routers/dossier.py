"""
routers/dossier.py — Endpoint de geração e recuperação do dossiê investigativo.

GET /alerts/{alert_id}/dossier:
  - 200 + dossiê  → dossiê já existe (cache — RN-14)
  - 202 Accepted  → dossiê está sendo gerado (status GERANDO)
  - 200 + dossiê  → primeira requisição: aciona DossierBuilder e retorna
  - 404           → alerta não existe

Regras de negócio: RN-01, RN-08, RN-14.
DossierBuilder implementado na ST-4 — este router já está preparado para recebê-lo.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.api.schemas.dossier import DossierResponse, InvestigationStep, RelatedCases
from src.models.db_models import Alert, Dossier

router = APIRouter()

_GENERATING_STATUSES = {"gerando", "GERANDO"}


def _dossier_to_response(dossier: Dossier) -> DossierResponse:
    """Deserializa as seções JSON do ORM para DossierResponse."""
    trace_raw = dossier.investigation_trace
    investigation_trace = None
    if trace_raw:
        try:
            steps = json.loads(trace_raw)
            investigation_trace = [InvestigationStep(**s) for s in steps]
        except Exception:
            investigation_trace = None

    related_cases = None
    if dossier.related_cases_json:
        try:
            related_cases = RelatedCases(**json.loads(dossier.related_cases_json))
        except Exception:
            related_cases = None

    return DossierResponse(
        alert_id=dossier.alert_id,
        dossier_id=dossier.dossier_id,
        generated_at=dossier.generated_at,
        status=dossier.status,
        model_version=dossier.model_version,
        transaction=json.loads(dossier.transaction_json) if dossier.transaction_json else None,
        customer=json.loads(dossier.customer_json) if dossier.customer_json else None,
        scoring=json.loads(dossier.scoring_json) if dossier.scoring_json else None,
        deviations=json.loads(dossier.deviations_json) if dossier.deviations_json else None,
        timeline=json.loads(dossier.timeline_json) if dossier.timeline_json else None,
        investigation_priority=dossier.investigation_priority,
        investigation_trace=investigation_trace,
        related_cases=related_cases,
        cluster_comparison=None,
        cluster_comparison_status=dossier.cluster_comparison_status or "nao_entregue_neste_ciclo",
        disclaimer=dossier.disclaimer,
        analyst_decision=dossier.analyst_decision,
        justification_text=dossier.justification_text,
        decision_at=dossier.decision_at,
    )


@router.get(
    "/alerts/{alert_id}/dossier",
    summary="Obter dossiê investigativo de um alerta",
    responses={
        200: {"description": "Dossiê pronto"},
        202: {"description": "Dossiê sendo gerado — tente novamente em instantes"},
        404: {"description": "Alerta não encontrado"},
    },
)
def get_dossier(
    alert_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    """Retorna o dossiê investigativo do alerta.

    Fluxo (docs/arquitetura.md Seção 7.1):
    1. Verifica se o alerta existe → 404 se não.
    2. Verifica cache na tabela dossiers → retorna se PRONTO (RN-14).
    3. Se status GERANDO → retorna 202.
    4. Se não existe → aciona DossierBuilder (ST-4) e retorna dossiê gerado.
    """
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alerta '{alert_id}' não encontrado.")

    # Verificar cache (RN-14)
    dossier = db.query(Dossier).filter(Dossier.alert_id == alert_id).first()

    if dossier is not None:
        if dossier.status.lower() in _GENERATING_STATUSES:
            response.status_code = 202
            return {"detail": "Dossiê sendo gerado. Tente novamente em instantes.", "alert_id": alert_id}
        return _dossier_to_response(dossier)

    # Sem dossiê: acionar DossierBuilder (implementado na ST-4)
    from src.services.dossier_builder import DossierBuilder  # import tardio: ST-4 stub
    builder = DossierBuilder(db)
    dossier = builder.build(alert_id)
    return _dossier_to_response(dossier)
