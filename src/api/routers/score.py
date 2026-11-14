"""
routers/score.py — Endpoint direto de scoring.

POST /score  →  recebe ScoreInput, retorna ScoreResult.

Regras de negócio relevantes:
  RN-02  score é float [0,1]
  RN-11  modo mock quando modelo não disponível e allow_mock=True (mock_mode: true)
  RN-16  fraud_score/visa_fraud_score/mc_fraud_score são features de ENTRADA

Guards de qualidade (v2):
  O ScoringService lança HTTPException diretamente para que sejam reconhecidas
  tanto pelo endpoint /score quanto por chamadas internas (ex.: DossierBuilder).
"""

from functools import lru_cache
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute

from src.api.schemas.score import ScoreInput, ScoreResult
from src.services.scoring_service import ScoringService

def _reject_nonfinite_json(value: str):
    raise ValueError(f"Número JSON não finito: {value}")


class _FiniteJsonRoute(APIRoute):
    """NaN/Infinity não são JSON válido; rejeita antes da validação Pydantic."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            if request.headers.get("content-type", "").split(";")[0] == "application/json":
                try:
                    json.loads(await request.body(), parse_constant=_reject_nonfinite_json)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise HTTPException(422, detail={"code": "invalid_json",
                        "mensagem": "JSON inválido ou número não finito; use null para ausências."}) from exc
            return await handler(request)

        return guarded


router = APIRouter(route_class=_FiniteJsonRoute)


@lru_cache(maxsize=1)
def _get_scoring_service() -> ScoringService:
    """Instância singleton do ScoringService — artefatos carregados uma vez.

    Usa allow_mock=False (default). Para compatibilidade retroativa com mocks,
    construa ScoringService(allow_mock=True) explicitamente.
    """
    svc = ScoringService()
    svc.load_artifacts()
    return svc


@router.post(
    "/score",
    response_model=ScoreResult,
    summary="Gerar score de risco para uma transação",
)
def compute_score(payload: ScoreInput) -> ScoreResult:
    """Recebe os campos de uma transação e retorna score de risco + fatores SHAP.

    Erros possíveis:
    - 422 insufficient_data: nenhum score externo finito fornecido.
    - 422 invalid_score_value: score externo não finito (NaN/inf).
    - 503 artifacts_unavailable: artefatos ausentes.
    - 503 artifact_checksum_mismatch: artefatos corrompidos.
    - 503 shap_*: falha no cálculo ou validação SHAP.
    - 503 model_*: saída do modelo inválida.

    Em modo mock (apenas se ScoringService foi construído com allow_mock=True):
    retorna score mock `0.5` com `mock_mode: true`.
    """
    return _get_scoring_service().predict(payload)
