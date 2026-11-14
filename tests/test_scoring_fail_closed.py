"""Falhas de integridade e JSON não finito devem produzir recusas explícitas."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import joblib
import numpy as np
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer

from src.api.routers import score as router_module
from src.services.scoring_service import ScoringService


NAMES = ["amount", "fraud_score", "visa_fraud_score", "mc_fraud_score", "mcc",
         "tipo_transacao", "modalidade_transacao", "pos_environment"]


@pytest.fixture
def bundle(tmp_path):
    model = DummyClassifier(strategy="prior").fit(np.zeros((2, 8)), [0, 1])
    pre = SimpleImputer().fit(np.zeros((2, 8)))
    explainer = SimpleNamespace(expected_value=.5, model_version_="v2.0.0")
    for name, value in [("model", model), ("pipeline", pre), ("explainer", explainer)]:
        value.model_version_ = "v2.0.0"
        joblib.dump(value, tmp_path / f"{name}_v1.pkl")
    (tmp_path / "feature_names.json").write_text(json.dumps(NAMES))
    (tmp_path / "thresholds.json").write_text(json.dumps({"baixo": .4, "alto": .7,
        "model_version": "v2.0.0", "artifact_schema_version": 2}))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    (tmp_path / "artifact_manifest.json").write_text(json.dumps({"model_version": "v2.0.0", "sha256": hashes}))
    return tmp_path


@pytest.mark.parametrize("mock_allowed", [False, True])
def test_missing_manifest_v2_is_not_accepted(bundle, mock_allowed):
    (bundle / "artifact_manifest.json").unlink()
    with pytest.raises(HTTPException) as error:
        ScoringService(bundle, allow_mock=mock_allowed).load_artifacts()
    assert error.value.status_code == 503 and error.value.detail["code"] == "manifest_required"


def test_partial_manifest_rejected_before_deserializing(bundle):
    path = bundle / "artifact_manifest.json"
    manifest = json.loads(path.read_text()); del manifest["sha256"]["explainer_v1.pkl"]
    path.write_text(json.dumps(manifest))
    with patch("joblib.load") as load:
        with pytest.raises(HTTPException) as error:
            ScoringService(bundle).load_artifacts()
        load.assert_not_called()
    assert error.value.status_code == 503


def test_corruption_never_activates_mock(bundle):
    with (bundle / "model_v1.pkl").open("ab") as out:
        out.write(b"corrupted-test")
    with pytest.raises(HTTPException) as error:
        ScoringService(bundle, allow_mock=True).load_artifacts()
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "artifact_checksum_mismatch"


@pytest.fixture
def client(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(router_module, "_get_scoring_service", lambda: service)
    app = FastAPI(); app.include_router(router_module.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_number_is_rejected_before_scoring(constant, client):
    http, service = client
    body = '{"event_external_id":"TEST","amount":'+constant+',"fraud_score":800}'
    response = http.post("/score", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_json"
    service.predict.assert_not_called()


def test_nonfinite_probability_cannot_escape_as_json_nan(client):
    http, service = client
    service.predict.side_effect = HTTPException(503, detail={"code": "model_non_finite_output"})
    response = http.post("/score", json={"event_external_id": "TEST", "amount": 100, "fraud_score": 800})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model_non_finite_output"
