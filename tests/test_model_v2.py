"""Regressões da auditoria: fixtures técnicas, sem artefatos oficiais."""
from pathlib import Path
import json
import hashlib
import shutil
import subprocess
import sys

import joblib
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import model_v2 as v2
from train_model_v2 import operating_thresholds


@pytest.fixture(scope="module")
def training_fixture():
    rng = np.random.default_rng(1234)
    n, positives = 500, 40
    y = np.r_[np.ones(positives, int), np.zeros(n-positives, int)]
    data = {"amount": rng.uniform(5, 150, n),
            "mcc": rng.choice([5411., 5732., 5912.], n),
            "tipo_transacao": rng.choice(["credito", "debito"], n),
            "modalidade_transacao": rng.choice(["cartao_nao_presente", "cartao_presente"], n),
            "pos_environment": rng.choice(["ECOMMERCE", "POS"], n),
            "currency_code": rng.choice(["BRL", None], n),
            "feedback_label": y, "event_external_id": [f"TECH_{i}" for i in range(n)]}
    for field in v2.EXTERNAL:
        data[field] = np.where(y, rng.uniform(780, 999, n), rng.uniform(0, 500, n))
    frame = pd.DataFrame(data)
    frame.loc[positives+5, "pos_environment"] = np.nan
    frame.loc[positives+6, "mcc"] = np.nan
    return frame


@pytest.fixture(scope="module")
def fitted(training_fixture):
    return v2.fit_candidate(training_fixture)


def test_issue1_required_features_reach_fitted_pipeline(fitted):
    pre, model, _ = fitted
    required = {"fraud_score", "visa_fraud_score", "mc_fraud_score", "amount",
                "modalidade_transacao", "pos_environment", "mcc", "currency_code"}
    assert required <= set(pre.feature_names_in_)
    assert pre.feature_names_in_.tolist() == v2.NAMES
    assert not {"feedback_label", "decision", "transaction_status", "fraud"} & set(pre.feature_names_in_)
    assert pre.model_version_ == model.model_version_ == "v2.1.0"


def test_currency_is_encoded_and_input_contract_preserves_it(fitted, training_fixture):
    from src.api.schemas.score import ScoreInput
    pre, _, _ = fitted
    row = training_fixture[v2.NAMES].iloc[[0]].copy()
    row["currency_code"] = "BRL"
    known = pre.transform(row)
    row["currency_code"] = "USD"
    unknown = pre.transform(row)
    assert not np.array_equal(known, unknown)
    payload = ScoreInput(event_external_id="CURRENCY_TEST", amount=10, currency_code="BRL")
    assert payload.model_dump()["currency_code"] == "BRL"


@pytest.mark.parametrize("column", v2.NAMES)
def test_nan_and_json_null_are_identical_for_every_input(column, fitted, training_fixture):
    pre, model, _ = fitted
    a = training_fixture[v2.NAMES].copy(); b = a.copy()
    a[column] = np.nan; b[column] = None
    np.testing.assert_array_equal(pre.transform(a), pre.transform(b))
    np.testing.assert_array_equal(v2.scores(pre, model, a), v2.scores(pre, model, b))


def test_mcc_numeric_and_string_and_amount_import_default_are_identical(fitted, training_fixture):
    pre, model, _ = fitted
    a = training_fixture[v2.NAMES].copy(); b = a.copy()
    a["amount"] = np.nan; b["amount"] = 0.
    b["mcc"] = b.mcc.map(lambda v: str(v) if pd.notna(v) else None)
    np.testing.assert_array_equal(pre.transform(a), pre.transform(b))


def test_neural_fit_and_natural_calibration_are_disjoint(fitted, training_fixture):
    pre, model, info = fitted
    fit = set(info["fit_event_ids"]); calibration = set(info["calibration_event_ids"])
    assert fit.isdisjoint(calibration)
    assert fit | calibration == set(training_fixture.event_external_id)
    assert info["calibration_rows"] == 100
    assert info["calibration_positives"] == 8
    assert info["augmentation"]["original_rows"] == info["fit_rows"]
    assert info["neural_input_features"] > 8
    assert info["augmentation"]["all_missing_augmented"] is False


def test_augmentation_preserves_inputs_labels_and_always_observed_score(fitted, training_fixture):
    pre, _, _ = fitted
    z = pre.transform(training_fixture[v2.NAMES]); before = z.copy()
    y = training_fixture.feedback_label.to_numpy()
    augmented, labels, info = v2.augment_training(z, y)
    np.testing.assert_array_equal(z, before)
    assert (augmented[:, 1:4] >= 0).any(axis=1).all()
    assert set(labels) == {0, 1}
    assert len(info["mask_rows"]) == 7
    assert set(info["mask_rows"]) == {str(i) for i in range(1, 8)}
    for value in augmented[labels == 1, 1:4].ravel():
        assert value == -1 or value >= 780
    for value in augmented[labels == 0, 1:4].ravel():
        assert value == -1 or value <= 500


@pytest.mark.parametrize("available", v2.EXTERNAL)
def test_ranks_positive_examples_with_only_one_external_score(available, fitted, training_fixture):
    pre, model, _ = fitted
    frame = training_fixture.copy()
    for field in v2.EXTERNAL:
        if field != available:
            frame[field] = None
    p = v2.scores(pre, model, frame)
    assert p[frame.feedback_label.eq(1)].min() > p[frame.feedback_label.eq(0)].max()


def test_nominal_unknowns_do_not_break_prediction(fitted, training_fixture):
    pre, model, _ = fitted
    frame = training_fixture[v2.NAMES].head(5).copy()
    frame["mcc"] = "9999.0"
    frame["tipo_transacao"] = "categoria_nova"
    frame["pos_environment"] = "canal_novo"
    frame["currency_code"] = "USD"
    p = v2.scores(pre, model, frame)
    assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()


def test_background_has_both_classes_correct_weights_and_no_calibration(fitted, training_fixture):
    pre, model, info = fitted
    fit = training_fixture.set_index("event_external_id", drop=False).loc[info["fit_event_ids"]]
    explainer = v2.build_explainer(pre, model, fit, size=64)
    assert set(explainer.background_event_ids_) <= set(info["fit_event_ids"])
    assert set(explainer.background_event_ids_).isdisjoint(info["calibration_event_ids"])
    npos = min(32, 64 // 4, int(fit.feedback_label.sum()))
    assert explainer.data.weights[:npos].sum() == pytest.approx(fit.feedback_label.mean())
    assert explainer.data.weights.sum() == pytest.approx(1.)
    z = pre.transform(training_fixture[v2.NAMES].iloc[:2])
    values = explainer.shap_values(z, silent=True)
    assert values.shape == (2, len(v2.NAMES))
    np.testing.assert_allclose(values.sum(axis=1)+explainer.expected_value, model.predict_proba(z)[:, 1], atol=1e-6)


def test_native_pickles_load_without_project_imports(fitted, training_fixture, tmp_path):
    pre, model, info = fitted
    fit = training_fixture.set_index("event_external_id", drop=False).loc[info["fit_event_ids"]]
    explainer = v2.build_explainer(pre, model, fit, size=32)
    for name, obj in [("pipeline", pre), ("model", model), ("explainer", explainer)]:
        joblib.dump(obj, tmp_path / f"{name}.pkl")
    rows = json.loads(training_fixture[v2.NAMES].head(2).to_json(orient="records"))
    code = '''
import sys,json,pathlib,joblib,numpy as np,pandas as pd
p=pathlib.Path(sys.argv[1]); x=pd.DataFrame(json.loads(sys.argv[2]))
pre=joblib.load(p/'pipeline.pkl'); m=joblib.load(p/'model.pkl'); e=joblib.load(p/'explainer.pkl')
z=pre.transform(x); s=e.shap_values(z,silent=True)
assert z.shape==(2,9) and s.shape==(2,9)
assert np.max(abs(s.sum(axis=1)+e.expected_value-m.predict_proba(z)[:,1]))<1e-6
print('PORTABLE_V2_OK')
'''
    completed = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path), json.dumps(rows)],
                               cwd=tmp_path, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert "PORTABLE_V2_OK" in completed.stdout
    # A versão protegida também precisa aceitar o contrato de nove features.
    from src.api.schemas.score import ScoreInput
    from src.services.scoring_service import ScoringService
    for name in ("pipeline", "model", "explainer"):
        shutil.copy2(tmp_path / f"{name}.pkl", tmp_path / f"{name}_v1.pkl")
    (tmp_path / "feature_names.json").write_text(json.dumps(v2.NAMES))
    (tmp_path / "thresholds.json").write_text(json.dumps({
        "baixo": .4, "alto": .7, "model_version": v2.VERSION, "artifact_schema_version": 2}))
    required = ["model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl", "feature_names.json", "thresholds.json"]
    (tmp_path / "artifact_manifest.json").write_text(json.dumps({"sha256": {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in required}}))
    service = ScoringService(tmp_path)
    for currency in ("BRL", "USD", None):
        row = dict(rows[0], event_external_id="CURRENCY", currency_code=currency)
        row["mcc"] = str(row["mcc"])
        result = service.predict(ScoreInput(**row))
        assert result.model_version == v2.VERSION and not result.mock_mode
        assert 0 <= result.fraud_score_model <= 1 and len(result.top_factors) == 5


def test_thresholds_have_explicit_operating_targets_and_refuse_bad_discrimination():
    y = np.r_[np.zeros(1000, int), np.ones(20, int)]
    p = np.r_[np.linspace(0.0001, .02, 1000), np.linspace(.80, .99, 20)]
    result = operating_thresholds(y, p)
    assert 0 < result["baixo"] < result["alto"] <= 1
    assert result["selection_source"] == "development_out_of_fold"
    assert result["operating_targets"]["business_approved"] is False
    with pytest.raises(RuntimeError):
        operating_thresholds(y, np.full(len(y), .5))
