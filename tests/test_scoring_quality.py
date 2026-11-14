"""
tests/test_scoring_quality.py — Testes técnicos dos guards de qualidade do ScoringService v2.

Cobre:
  QG-01  Ausência de todos os scores externos → 422 insufficient_data
  QG-02  Um ou dois scores → quality_status='degraded' + warning de calibração
  QG-03  Score externo não finito (NaN/±inf) → 422 invalid_score_value
  QG-04  Resultado do modelo não finito ou classes incorretas → 503
  QG-05  Checksum SHA256 corrompido → 503 artifact_checksum_mismatch
  QG-06  Artefatos ausentes com allow_mock=False → 503 artifacts_unavailable
         Artefatos ausentes com allow_mock=True → mock_mode=True
  QG-07  SHAP falha → 503; shape errado → 503; aditividade > 1e-6 → 503
  QG-08  SHAP shape (1,8) aceito; mock e real discriminados

Todos os testes usam stubs/mocks — não requerem artefatos reais em disco.
Testes de endpoint usam FastAPI TestClient.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.api.schemas.score import ScoreInput, ScoreResult
from src.services.scoring_service import ScoringService, HTTPException as ServiceHTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEATURE_NAMES = [
    "amount", "fraud_score", "visa_fraud_score", "mc_fraud_score",
    "mcc", "tipo_transacao", "modalidade_transacao", "pos_environment",
]

THRESHOLDS = {"baixo": 0.40, "alto": 0.70, "model_version": "v2.0.0-test"}


def _make_input(**overrides) -> ScoreInput:
    """Cria ScoreInput completo (todos 3 scores externos presentes por padrão)."""
    defaults: dict[str, Any] = {
        "event_external_id": "TX-TEST-001",
        "amount": 150.0,
        "fraud_score": 500.0,
        "visa_fraud_score": 300.0,
        "mc_fraud_score": 200.0,
        "tipo_transacao": "credito",
        "modalidade_transacao": "cartao_nao_presente",
        "pos_environment": "ECOMMERCE",
        "mcc": "5411",
    }
    defaults.update(overrides)
    return ScoreInput(**defaults)


def _make_service_with_stubs(
    *,
    allow_mock: bool = False,
    model_classes: Any = None,
    proba: Any = None,
    shap_values: Any = None,
    shap_expected_value: float = 0.0,
    pipeline_output: Any = None,
    thresholds: dict | None = None,
    min_external_scores: int = 1,
) -> ScoringService:
    """Cria ScoringService com stubs injetados sem carregar artefatos reais."""
    if model_classes is None:
        model_classes = np.array([0, 1])
    if proba is None:
        # score base finito em [0,1] → 0.6
        proba = np.array([[0.4, 0.6]])
    if pipeline_output is None:
        pipeline_output = np.ones((1, 8), dtype=float) * 0.5
    if shap_values is None:
        # shape (1,8), soma = 0.6 - base_value para aditividade OK
        base = shap_expected_value
        vals = np.array([[0.1, 0.05, -0.02, 0.03, 0.04, 0.01, -0.01, 0.0]])
        # ajusta último para que soma + base = proba[0][1]
        target_proba = float(proba[0][1])
        vals[0, -1] = target_proba - base - vals[0, :-1].sum()
        shap_values = vals
    if thresholds is None:
        thresholds = dict(THRESHOLDS)

    # Pipeline stub
    pipeline_stub = MagicMock()
    pipeline_stub.transform.return_value = pipeline_output

    # Model stub
    model_stub = MagicMock()
    model_stub.classes_ = model_classes
    model_stub.predict_proba.return_value = proba

    # Explainer stub
    explainer_stub = MagicMock()
    explainer_stub.shap_values.return_value = shap_values
    explainer_stub.expected_value = shap_expected_value

    svc = ScoringService(models_dir=pathlib.Path("nonexistent"), allow_mock=allow_mock)
    svc._pipeline = pipeline_stub
    svc._model = model_stub
    svc._explainer = explainer_stub
    svc._feature_names = FEATURE_NAMES
    svc._thresholds = thresholds
    svc._model_version = thresholds.get("model_version", "v2-stub")
    svc._loaded = True
    svc._mock_mode = False
    svc._min_external_scores = min_external_scores
    return svc


# ---------------------------------------------------------------------------
# QG-01 — Ausência de todos os scores externos
# ---------------------------------------------------------------------------

class TestQG01InsufficientData:
    """QG-01: ausência de todos os scores externos → 422 insufficient_data."""

    def test_all_external_scores_none_raises_422(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=None, visa_fraud_score=None, mc_fraud_score=None)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        err = exc_info.value
        assert err.status_code == 422
        assert err.detail["code"] == "insufficient_data"

    def test_error_detail_has_portuguese_message(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=None, visa_fraud_score=None, mc_fraud_score=None)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert "mensagem" in exc_info.value.detail
        msg = exc_info.value.detail["mensagem"]
        assert len(msg) > 10  # mensagem não vazia

    def test_error_detail_has_campos_faltantes(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=None, visa_fraud_score=None, mc_fraud_score=None)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        detail = exc_info.value.detail
        assert "campos_faltantes" in detail
        assert isinstance(detail["campos_faltantes"], list)
        assert len(detail["campos_faltantes"]) > 0

    def test_never_returns_low_score_when_no_external(self):
        """Sem scores externos nunca pode retornar score baixo (nem mock silencioso)."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=None, visa_fraud_score=None, mc_fraud_score=None)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        # Deve ser 422, nunca retornar ScoreResult
        assert exc_info.value.status_code == 422

    def test_min_external_scores_respected_from_thresholds(self):
        """min_external_scores=2 exige pelo menos 2 scores."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs(min_external_scores=2)
        # Somente 1 score disponível
        inp = _make_input(fraud_score=500.0, visa_fraud_score=None, mc_fraud_score=None)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "insufficient_data"

    def test_min_external_scores_1_allows_single_score(self):
        """min_external_scores=1 (default) aceita com um único score."""
        svc = _make_service_with_stubs(min_external_scores=1)
        inp = _make_input(fraud_score=500.0, visa_fraud_score=None, mc_fraud_score=None)
        result = svc.predict(inp)
        assert isinstance(result, ScoreResult)
        assert not result.mock_mode


# ---------------------------------------------------------------------------
# QG-02 — quality_status e warnings
# ---------------------------------------------------------------------------

class TestQG02QualityStatus:
    """QG-02: um/dois scores → degraded + warning; três → complete."""

    def test_all_three_scores_gives_complete(self):
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=500.0, visa_fraud_score=300.0, mc_fraud_score=200.0)
        result = svc.predict(inp)
        assert result.quality_status == "complete"
        assert result.available_external_scores == 3
        assert result.quality_warnings == []

    def test_one_score_gives_degraded(self):
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=500.0, visa_fraud_score=None, mc_fraud_score=None)
        result = svc.predict(inp)
        assert result.quality_status == "degraded"
        assert result.available_external_scores == 1

    def test_one_score_gives_calibration_warning(self):
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=500.0, visa_fraud_score=None, mc_fraud_score=None)
        result = svc.predict(inp)
        assert len(result.quality_warnings) > 0
        # Aviso deve mencionar calibração ou certificação
        warning_text = " ".join(result.quality_warnings).lower()
        assert "calibra" in warning_text or "certif" in warning_text

    def test_two_scores_gives_degraded(self):
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=500.0, visa_fraud_score=300.0, mc_fraud_score=None)
        result = svc.predict(inp)
        assert result.quality_status == "degraded"
        assert result.available_external_scores == 2

    def test_two_scores_gives_calibration_warning(self):
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=500.0, visa_fraud_score=300.0, mc_fraud_score=None)
        result = svc.predict(inp)
        assert len(result.quality_warnings) > 0
        warning_text = " ".join(result.quality_warnings).lower()
        assert "calibra" in warning_text or "certif" in warning_text

    def test_quality_fields_default_for_backward_compat(self):
        """ScoreResult pode ser instanciado sem campos de qualidade (compatibilidade)."""
        result = ScoreResult(
            event_external_id="TX-001",
            fraud_score_model=0.6,
            risk_level="medio",
            top_factors=[],
            model_version="v1.0.0",
        )
        assert result.quality_status == "complete"
        assert result.available_external_scores == 0
        assert result.quality_warnings == []

    def test_mock_mode_explicit_quality_degraded(self):
        """Modo mock retorna quality_status degraded e mock_mode=True."""
        svc = ScoringService(allow_mock=True)
        svc._mock_mode = True
        svc._loaded = True
        inp = _make_input()
        result = svc.predict(inp)
        assert result.mock_mode is True
        assert result.quality_status == "degraded"


# ---------------------------------------------------------------------------
# QG-03 — Rejeição de scores externos não finitos
# ---------------------------------------------------------------------------

class TestQG03NonFiniteInputRejection:
    """QG-03: scores externos não finitos na entrada → 422 invalid_score_value."""

    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
    def test_nan_inf_in_fraud_score_rejected(self, bad_value):
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        # Bypass Pydantic validation — inject via dict para testar o guard do serviço
        inp = ScoreInput(
            event_external_id="TX-001",
            amount=100.0,
            visa_fraud_score=300.0,
            mc_fraud_score=200.0,
        )
        # Sobrescreve o campo depois da criação para simular valor inválido recebido
        object.__setattr__(inp, "fraud_score", bad_value)
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "invalid_score_value"

    def test_valid_boundary_values_accepted(self):
        """Valores de fronteira válidos (0.0 e 1000.0) devem ser aceitos."""
        svc = _make_service_with_stubs()
        inp = _make_input(fraud_score=0.0, visa_fraud_score=1000.0, mc_fraud_score=500.0)
        result = svc.predict(inp)
        assert isinstance(result, ScoreResult)


# ---------------------------------------------------------------------------
# QG-04 — Resultado do modelo inválido
# ---------------------------------------------------------------------------

class TestQG04ModelOutputValidation:
    """QG-04: saída do modelo não finita ou classes incorretas → 503 (sem clamp)."""

    def test_non_finite_proba_raises_503(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs(proba=np.array([[0.4, float("nan")]]))
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "model_non_finite_output"

    def test_proba_above_1_raises_503(self):
        """Probabilidade > 1.0 deve lançar 503, não ser clampeada silenciosamente."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs(proba=np.array([[0.0, 1.5]]))
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "model_out_of_range"

    def test_proba_below_0_raises_503(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs(proba=np.array([[1.2, -0.2]]))
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "model_out_of_range"

    def test_wrong_classes_raises_503(self):
        """Classes diferentes de [0, 1] → 503."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs(
            model_classes=np.array([1, 2]),
            proba=np.array([[0.4, 0.6]]),
        )
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "model_classes_error"

    def test_pipeline_wrong_shape_raises_503(self):
        """Pipeline retornando shape diferente de (1,8) → 503."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs(
            pipeline_output=np.ones((1, 12), dtype=float),  # wrong n_features
        )
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "pipeline_shape_error"

    def test_pipeline_nan_raises_503(self):
        """Pipeline com NaN → 503."""
        from fastapi import HTTPException
        bad_output = np.ones((1, 8), dtype=float)
        bad_output[0, 3] = float("nan")
        svc = _make_service_with_stubs(pipeline_output=bad_output)
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "pipeline_non_finite"


# ---------------------------------------------------------------------------
# QG-05 — Checksum SHA256
# ---------------------------------------------------------------------------

class TestQG05ChecksumValidation:
    """QG-05: checksums SHA256 corrompidos → 503 artifact_checksum_mismatch."""

    def _write_minimal_artifacts(self, tmp_path: pathlib.Path) -> None:
        """Escreve os 5 artefatos necessários com conteúdo válido mínimo."""
        import joblib
        from sklearn.impute import SimpleImputer
        from sklearn.dummy import DummyClassifier
        from types import SimpleNamespace
        pipeline = SimpleImputer().fit(np.zeros((2, 8)))
        pipeline.model_version_ = "v2.0.0-test"
        joblib.dump(pipeline, tmp_path / "pipeline_v1.pkl")
        model = DummyClassifier(strategy="prior").fit(np.zeros((2, 8)), [0, 1])
        model.model_version_ = "v2.0.0-test"
        joblib.dump(model, tmp_path / "model_v1.pkl")
        explainer = SimpleNamespace(expected_value=0.5, model_version_="v2.0.0-test")
        joblib.dump(explainer, tmp_path / "explainer_v1.pkl")

        (tmp_path / "feature_names.json").write_text(json.dumps(FEATURE_NAMES))
        (tmp_path / "thresholds.json").write_text(json.dumps(THRESHOLDS))

    def _compute_sha256(self, path: pathlib.Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    def test_correct_checksums_accepted(self, tmp_path):
        """Checksums corretos → artefatos aceitos."""
        self._write_minimal_artifacts(tmp_path)
        sha_map = {}
        for name in ("model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
                      "feature_names.json", "thresholds.json"):
            sha_map[name] = self._compute_sha256(tmp_path / name)
        manifest = {"model_version": "v2.0.0-test", "sha256": sha_map, "fresh_process_validation": True}
        (tmp_path / "artifact_manifest.json").write_text(json.dumps(manifest))

        svc = ScoringService(models_dir=tmp_path, allow_mock=False)
        # Não deve lançar exceção
        svc.load_artifacts()
        assert svc._loaded is True
        assert svc._mock_mode is False

    def test_corrupted_model_raises_503(self, tmp_path):
        """SHA256 incorreto para model_v1.pkl → 503."""
        from fastapi import HTTPException
        self._write_minimal_artifacts(tmp_path)
        sha_map = {}
        for name in ("model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
                      "feature_names.json", "thresholds.json"):
            sha_map[name] = self._compute_sha256(tmp_path / name)
        # Corrompe o hash do modelo
        sha_map["model_v1.pkl"] = "0" * 64
        manifest = {"model_version": "v2.0.0-test", "sha256": sha_map, "fresh_process_validation": True}
        (tmp_path / "artifact_manifest.json").write_text(json.dumps(manifest))

        svc = ScoringService(models_dir=tmp_path, allow_mock=False)
        with pytest.raises(HTTPException) as exc_info:
            svc.load_artifacts()
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "artifact_checksum_mismatch"

    def test_manifest_v1_without_sha256_section_skips_check(self, tmp_path):
        """Manifest v1 sem seção sha256 → verificação ignorada (compatibilidade)."""
        self._write_minimal_artifacts(tmp_path)
        (tmp_path / "thresholds.json").write_text(json.dumps({**THRESHOLDS, "model_version": "v1.0.0"}))
        # Manifest v1 sem sha256
        manifest = {"model_version": "v1.0.0", "fresh_process_validation": True}
        (tmp_path / "artifact_manifest.json").write_text(json.dumps(manifest))

        svc = ScoringService(models_dir=tmp_path, allow_mock=False)
        svc.load_artifacts()
        assert svc._loaded is True

    def test_missing_manifest_skips_check(self, tmp_path):
        """Sem artifact_manifest.json → verificação de checksum ignorada."""
        self._write_minimal_artifacts(tmp_path)
        (tmp_path / "thresholds.json").write_text(json.dumps({**THRESHOLDS, "model_version": "v1.0.0"}))
        svc = ScoringService(models_dir=tmp_path, allow_mock=False)
        svc.load_artifacts()
        assert svc._loaded is True


# ---------------------------------------------------------------------------
# QG-06 — allow_mock e artefatos ausentes
# ---------------------------------------------------------------------------

class TestQG06AllowMock:
    """QG-06: artefatos ausentes com allow_mock=False → 503; allow_mock=True → mock."""

    def test_missing_artifacts_allow_mock_false_raises_503(self, tmp_path):
        from fastapi import HTTPException
        svc = ScoringService(models_dir=tmp_path, allow_mock=False)
        with pytest.raises(HTTPException) as exc_info:
            svc.load_artifacts()
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "artifacts_unavailable"

    def test_missing_artifacts_allow_mock_true_gives_mock(self, tmp_path):
        svc = ScoringService(models_dir=tmp_path, allow_mock=True)
        svc.load_artifacts()
        assert svc._mock_mode is True
        assert svc._loaded is True

    def test_mock_result_has_mock_mode_true(self, tmp_path):
        svc = ScoringService(models_dir=tmp_path, allow_mock=True)
        svc.load_artifacts()
        inp = _make_input()
        result = svc.predict(inp)
        assert result.mock_mode is True

    def test_mock_result_is_not_blocked_by_insufficient_data_guard(self, tmp_path):
        """Mock mode ignora QG-01: aceita entrada sem scores externos."""
        svc = ScoringService(models_dir=tmp_path, allow_mock=True)
        svc.load_artifacts()
        inp = _make_input(fraud_score=None, visa_fraud_score=None, mc_fraud_score=None)
        result = svc.predict(inp)
        assert result.mock_mode is True
        assert result.fraud_score_model == 0.5

    def test_real_mode_default_is_allow_mock_false(self):
        """ScoringService() sem argumento deve usar allow_mock=False."""
        svc = ScoringService()
        assert svc._allow_mock is False


# ---------------------------------------------------------------------------
# QG-07/QG-08 — SHAP obrigatório, shape (1,8), aditividade
# ---------------------------------------------------------------------------

class TestQG07_08SHAP:
    """QG-07/QG-08: SHAP falha/shape inválido/aditividade > 1e-6 → 503."""

    def test_shap_exception_raises_503(self):
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        svc._explainer.shap_values.side_effect = RuntimeError("SHAP broke")
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "shap_computation_error"

    def test_shap_wrong_shape_raises_503(self):
        """SHAP com 12 features em vez de 8 → 503."""
        from fastapi import HTTPException
        bad_shap = np.ones((1, 12), dtype=float) * 0.05
        svc = _make_service_with_stubs(shap_values=bad_shap)
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "shap_shape_error"

    def test_shap_non_finite_raises_503(self):
        from fastapi import HTTPException
        bad_shap = np.ones((1, 8), dtype=float) * 0.05
        bad_shap[0, 0] = float("nan")
        svc = _make_service_with_stubs(shap_values=bad_shap)
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "shap_non_finite"

    def test_shap_additivity_error_raises_503(self):
        """Aditividade SHAP > 1e-6 → 503 shap_additivity_error."""
        from fastapi import HTTPException
        # shap_values que somam para valor muito diferente da proba
        # proba[0][1] = 0.6; base = 0.0; shap sum = 0.9 → err = 0.3
        bad_shap = np.ones((1, 8), dtype=float) * 0.1125  # sum = 0.9, base=0, proba=0.6
        svc = _make_service_with_stubs(
            shap_values=bad_shap,
            shap_expected_value=0.0,
            proba=np.array([[0.4, 0.6]]),
        )
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "shap_additivity_error"

    def test_shap_valid_shape_1_8_accepted(self):
        """Shape (1,8) com aditividade OK aceito corretamente."""
        base = 0.0
        proba_val = 0.6
        vals = np.zeros((1, 8), dtype=float)
        vals[0, 0] = proba_val - base  # aditividade perfeita
        svc = _make_service_with_stubs(
            shap_values=vals,
            shap_expected_value=base,
            proba=np.array([[0.4, proba_val]]),
        )
        inp = _make_input()
        result = svc.predict(inp)
        assert isinstance(result, ScoreResult)
        assert len(result.top_factors) == 5  # min(5, 8)

    def test_explainer_none_raises_503(self):
        """Explainer ausente (None) → 503 explainer_unavailable."""
        from fastapi import HTTPException
        svc = _make_service_with_stubs()
        svc._explainer = None
        inp = _make_input()
        with pytest.raises(HTTPException) as exc_info:
            svc.predict(inp)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "explainer_unavailable"

    def test_shap_list_format_class1_accepted(self):
        """SHAP retornado como lista [shap_class0, shap_class1] → usa classe 1."""
        base = 0.0
        proba_val = 0.6
        vals_class1 = np.zeros((1, 8), dtype=float)
        vals_class1[0, 0] = proba_val - base
        vals_class0 = -vals_class1  # oposto

        svc = _make_service_with_stubs(
            shap_values=[vals_class0, vals_class1],
            shap_expected_value=base,
            proba=np.array([[0.4, proba_val]]),
        )
        inp = _make_input()
        result = svc.predict(inp)
        assert isinstance(result, ScoreResult)


# ---------------------------------------------------------------------------
# FastAPI TestClient — testes de endpoint
# ---------------------------------------------------------------------------

class TestScoringEndpoint:
    """Testes do endpoint POST /score via FastAPI TestClient."""

    @pytest.fixture
    def app_with_stub_service(self):
        """Cria app FastAPI com ScoringService injetado via dependency override."""
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")

        from src.api.routers import score as score_router_module

        app = FastAPI()

        # Stub service com modelo válido
        stub_svc = _make_service_with_stubs()

        # Override do singleton para injetar o stub
        score_router_module._get_scoring_service.cache_clear()
        original = score_router_module._get_scoring_service

        def _stub_svc():
            return stub_svc

        score_router_module._get_scoring_service = _stub_svc
        app.include_router(score_router_module.router)

        client = TestClient(app, raise_server_exceptions=False)
        yield client

        # Restaura
        score_router_module._get_scoring_service = original

    def test_valid_input_returns_200(self, app_with_stub_service):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")
        client = app_with_stub_service
        payload = {
            "event_external_id": "TX-E2E-001",
            "amount": 200.0,
            "fraud_score": 450.0,
            "visa_fraud_score": 300.0,
            "mc_fraud_score": 100.0,
            "tipo_transacao": "credito",
            "modalidade_transacao": "cartao_presente",
            "pos_environment": "POS",
            "mcc": "5411",
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "fraud_score_model" in data
        assert "quality_status" in data
        assert "available_external_scores" in data
        assert "quality_warnings" in data

    def test_missing_all_external_scores_returns_422(self, app_with_stub_service):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")
        client = app_with_stub_service
        payload = {
            "event_external_id": "TX-E2E-002",
            "amount": 200.0,
            # Sem scores externos
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 422

    def test_all_three_scores_returns_complete_status(self, app_with_stub_service):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")
        client = app_with_stub_service
        payload = {
            "event_external_id": "TX-E2E-003",
            "amount": 200.0,
            "fraud_score": 450.0,
            "visa_fraud_score": 300.0,
            "mc_fraud_score": 100.0,
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["quality_status"] == "complete"
        assert data["available_external_scores"] == 3
        assert data["quality_warnings"] == []

    def test_one_score_returns_degraded_status(self, app_with_stub_service):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")
        client = app_with_stub_service
        payload = {
            "event_external_id": "TX-E2E-004",
            "amount": 200.0,
            "fraud_score": 450.0,
            # visa_fraud_score e mc_fraud_score ausentes
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["quality_status"] == "degraded"
        assert data["available_external_scores"] == 1
        assert len(data["quality_warnings"]) > 0

    def test_response_has_top_factors(self, app_with_stub_service):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/httpx não instalados")
        client = app_with_stub_service
        payload = {
            "event_external_id": "TX-E2E-005",
            "amount": 200.0,
            "fraud_score": 450.0,
            "visa_fraud_score": 300.0,
            "mc_fraud_score": 100.0,
        }
        resp = client.post("/score", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "top_factors" in data
        assert len(data["top_factors"]) > 0
        for factor in data["top_factors"]:
            assert "feature" in factor
            assert "direction" in factor
            assert "shap_value" in factor
