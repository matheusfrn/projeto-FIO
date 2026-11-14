"""Testes técnicos com dados artificiais temporários; nunca geram artefatos oficiais."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import joblib
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import train_model as tm
import validate_artifacts as va


@pytest.fixture
def frame():
    rng = np.random.default_rng(123)
    n = 240
    df = pd.DataFrame({
        "amount": rng.uniform(1, 100, n),
        "fraud_score": rng.uniform(0, 999, n),
        "visa_fraud_score": rng.uniform(0, 999, n),
        "mc_fraud_score": rng.uniform(0, 999, n),
        "mcc": rng.choice([5411., 5732., 4111.], n),
        "tipo_transacao": rng.choice(["credito", "debito"], n),
        "modalidade_transacao": rng.choice(["cartao_nao_presente", "cartao_presente"], n),
        "pos_environment": rng.choice(["ECOMMERCE", "POS"], n),
        "feedback_label": [1] * 24 + [0] * (n - 24),
        "event_external_id": [f"FIXTURE_{i}" for i in range(n)],
        "_source_row": np.arange(n),
    })
    df.loc[2:5, "amount"] = np.nan
    df.loc[8, "pos_environment"] = np.nan
    return df


def test_prepare_filters_unlabeled_and_future_without_using_leakage(frame):
    raw = frame.copy()
    raw["event_occurred_at"] = "2025-01-10T12:00:00Z"
    raw.loc[0, "event_occurred_at"] = "2025-03-02 12:00:00+00:00"
    raw.loc[1, "event_occurred_at"] = "2025-04-01T00:00:00Z"
    raw.loc[2, "event_occurred_at"] = "invalid"
    raw.loc[3, "feedback_label"] = np.nan
    raw["decision"] = "deny"
    raw["dispute_outcome"] = "fraud"
    prepared = tm.prepare_dataset(raw)
    ids = set(prepared.event_external_id)
    assert "FIXTURE_0" in ids
    assert not {"FIXTURE_1", "FIXTURE_2", "FIXTURE_3"} & ids
    assert not {"decision", "dispute_outcome", "event_occurred_at"} & set(tm.FEATURE_NAMES)
    assert prepared.attrs["missing_or_invalid_labels"] == 1
    assert prepared.attrs["invalid_timestamps_labeled"] == 1


def test_demo_and_holdout_are_disjoint_and_reproducible(frame):
    rest, demo = tm.reserve_demo_cases(frame)
    _, repeated = tm.reserve_demo_cases(frame)
    dev, holdout = tm.stratified_holdout(rest)
    assert demo.event_external_id.tolist() == repeated.event_external_id.tolist()
    assert len(demo) == 6 and demo.feedback_label.eq(1).all()
    parts = [set(part.event_external_id) for part in (dev, holdout, demo)]
    assert not (parts[0] & parts[1] or parts[0] & parts[2] or parts[1] & parts[2])
    assert sum(map(len, parts)) == len(frame)


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "negative", "too_few"])
def test_invalid_demo_ids_are_rejected(frame, kind):
    ids = frame.loc[frame.feedback_label.eq(1), "event_external_id"].head(6).tolist()
    if kind == "duplicate":
        ids[-1] = ids[0]
    elif kind == "unknown":
        ids[-1] = "DOES_NOT_EXIST"
    elif kind == "negative":
        ids[-1] = "FIXTURE_100"
    else:
        ids.pop()
    with pytest.raises(ValueError):
        tm.reserve_demo_cases(frame, provided_ids=ids)


def test_preprocessing_handles_unknown_missing_and_numeric_strings_without_refit(frame):
    pre = tm.make_pipeline().named_steps["preprocessor"]
    pre.fit(frame[tm.FEATURE_NAMES])
    medians = pre.named_transformers_["num"].named_steps["imputer"].statistics_.copy()
    expected = frame[tm.NUMERIC_FEATURES].median().values
    np.testing.assert_allclose(medians, expected)
    row = {c: None for c in tm.FEATURE_NAMES}
    row.update(amount=1e9, mcc="5411.0", tipo_transacao="unseen_category")
    transformed = pre.transform(pd.DataFrame([row], columns=tm.FEATURE_NAMES))
    assert transformed.shape == (1, 8) and np.isfinite(transformed).all()
    np.testing.assert_array_equal(
        pre.named_transformers_["num"].named_steps["imputer"].statistics_, medians
    )
    assert pre.get_feature_names_out().tolist() == tm.FEATURE_NAMES


def test_oversampling_keeps_all_negatives_and_adds_only_existing_positives(frame):
    x, y = tm.oversample_train(frame[tm.FEATURE_NAMES], frame.feedback_label, ratio=0.5)
    assert len(x) > len(frame)
    pd.testing.assert_frame_equal(
        x.loc[y.eq(0)].reset_index(drop=True),
        frame.loc[frame.feedback_label.eq(0), tm.FEATURE_NAMES].reset_index(drop=True),
    )
    positives = {tuple(row) for row in frame.loc[frame.feedback_label.eq(1), tm.FEATURE_NAMES]
                 .fillna("MISSING").to_numpy()}
    assert all(tuple(row) in positives for row in x.loc[len(frame):]
               .fillna("MISSING").to_numpy())


def test_cv_fits_preprocessor_only_on_each_training_partition(frame, monkeypatch):
    original = tm.make_pipeline
    seen = []
    def instrumented():
        pipeline = original()
        prep = pipeline.named_steps["preprocessor"]
        fit = prep.fit
        def recording(x, y=None):
            seen.append(set(x.index))
            return fit(x, y)
        prep.fit = recording
        return pipeline
    monkeypatch.setattr(tm, "make_pipeline", instrumented)
    result = tm.run_cv(frame)
    assert len(result["folds"]) == 3 and len(seen) == 3
    assert all(len(indices) == 160 for indices in seen)
    assert all(sum(i in indices for indices in seen) == 2 for i in frame.index)


@pytest.fixture
def published_bundle(frame, tmp_path):
    rest, demo = tm.reserve_demo_cases(frame)
    dev, holdout = tm.stratified_holdout(rest)
    pre, model = tm.fit_final_model(dev)
    explainer = tm.build_explainer(pre, model, dev, n_background=4)
    assert set(explainer.background_event_ids_) <= set(dev.event_external_id)
    source = tmp_path / "technical_fixture.csv"
    frame.to_csv(source, index=False)
    baseline = tm.compute_baseline(dev, holdout)
    metrics = tm.build_metrics_json(
        {"mean_ap": 0.0, "std_ap": 0.0}, tm.evaluate_holdout(pre, model, holdout),
        baseline, frame, dev, holdout, demo, source, model,
    )
    output = tmp_path / "models"
    tm.save_artifacts(output, pre, model, explainer, metrics, demo, source)
    return output, pre, model, explainer


def test_all_five_artifacts_reload_in_fresh_process_without_training_module(published_bundle):
    output, pre, model, explainer = published_bundle
    names = json.loads((output / "feature_names.json").read_text())
    assert names == tm.FEATURE_NAMES
    assert json.loads((output / "thresholds.json").read_text())["alto"] == 0.7
    for name in ("model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl"):
        assert (output / name).is_file()
    manifest = json.loads((output / "artifact_manifest.json").read_text())
    assert manifest["fresh_process_validation"]
    for name, digest in manifest["sha256"].items():
        assert tm.sha256_file(output / name) == digest
    completed = subprocess.run(
        [sys.executable, "-I", str(Path(va.__file__).resolve()), "--models", str(output)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_failed_validation_does_not_replace_previous_artifacts(published_bundle, monkeypatch):
    output, pre, model, explainer = published_bundle
    before = (output / "model_v1.pkl").read_bytes()
    metrics = json.loads((output / "metrics.json").read_text())
    demo = pd.DataFrame({"event_external_id": ["TEST"], "_source_row": [0]})
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "validation")
    monkeypatch.setattr(tm.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        tm.save_artifacts(output, pre, model, explainer, metrics, demo, Path("unused"))
    assert (output / "model_v1.pkl").read_bytes() == before


def test_validator_requires_explainer(published_bundle, tmp_path):
    output, *_ = published_bundle
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    for name in ("model_v1.pkl", "pipeline_v1.pkl", "feature_names.json", "thresholds.json"):
        shutil.copy2(output / name, incomplete / name)
    with pytest.raises(va.ValidationError, match="explainer"):
        va.load_artifacts(incomplete)


def test_validator_detects_explainer_from_a_different_model(published_bundle, frame):
    output, *_ = published_bundle
    arts = va.load_artifacts(output)
    arts["model"].intercepts_[-1] += 2.0
    x = arts["pipeline"].transform(frame[tm.FEATURE_NAMES].iloc[:3])
    with pytest.raises(va.ValidationError, match="C3"):
        va.check_shap(arts, x, arts["feature_names"])


def test_top_k_and_confusion_matrix_handle_small_samples():
    precision, recall = tm.precision_recall_at_k(
        np.array([1, 0]), np.array([0.9, 0.1]), k=100
    )
    assert precision == 0.5 and recall == 1.0
    result = tm.evaluate_at_threshold(np.zeros(3, dtype=int), np.zeros(3), 0.4)
    assert result["confusion_matrix"] == [[3, 0], [0, 0]]
