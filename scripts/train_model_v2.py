"""Treina e avalia a v2 em pasta candidata; nunca sobrescreve models/.

Protocolo pré-definido em docs/protocolo-modelo-v2.1.json. O holdout da v1
é tratado como regressão conhecida, sem alegar uma nova avaliação cega.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from scipy.stats import beta
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.tree import DecisionTreeClassifier

import model_v2 as v2
import train_model as legacy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("shap").setLevel(logging.ERROR)
log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return legacy.sha256_file(path)


def interval(k: int, n: int) -> list[float]:
    return [float(beta.ppf(.025, k, n-k+1)) if k else 0.,
            float(beta.ppf(.975, k+1, n-k)) if k < n else 1.]


def evaluate(y, probability, cutoff: float = .4) -> dict:
    y, probability = np.asarray(y, int), np.asarray(probability, float)
    if not np.isfinite(probability).all():
        raise ValueError("Predição não finita")
    tn, fp, fn, tp = confusion_matrix(y, probability >= cutoff, labels=[0, 1]).ravel()
    order = np.argsort(-probability, kind="stable")[:min(100, len(y))]
    return {
        "n": len(y), "positives": int(y.sum()), "cutoff": float(cutoff),
        "ap": float(np.clip(average_precision_score(y, probability), 0., 1.)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "brier": float(brier_score_loss(y, probability)),
        "recall": float(tp / (tp+fn)) if tp+fn else 0.,
        "precision": float(tp / (tp+fp)) if tp+fp else 0.,
        "fpr": float(fp / (fp+tn)) if fp+tn else 0.,
        "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
        "precision_at_100": float(y[order].mean()),
        "recall_at_100": float(y[order].sum() / y.sum()),
        "recall_ci95_binomial": interval(int(tp), int(tp+fn)),
    }


def read_data(path: Path):
    header = pd.read_csv(path, nrows=0).columns.tolist()
    cols = v2.NAMES + ["event_external_id", "feedback_label", "event_occurred_at", "customer_id"]
    raw = pd.read_csv(path, usecols=cols, low_memory=False)
    raw.attrs.update(raw_rows=len(raw), raw_columns=len(header))
    prepared = legacy.prepare_dataset(raw)
    # A preparação histórica seleciona oito features. A moeda deve ser
    # recuperada do CSV, por ID, para entrar de fato no ajuste e na inferência.
    metadata = raw.set_index("event_external_id")[["event_occurred_at", "customer_id", "currency_code"]]
    prepared = prepared.join(metadata, on="event_external_id")
    prepared["timestamp"] = pd.to_datetime(prepared.event_occurred_at, format="mixed", utc=True)
    return prepared, {"raw_rows": len(raw), "raw_columns": len(header),
                      "labeled_period_rows": len(prepared), "sha256": sha(path)}


def reference_baselines(training, validation, pre=None) -> dict:
    valid = v2.availability(validation) > 0
    frame = validation.loc[valid]
    y = frame.feedback_label.to_numpy(int)
    medians = training[v2.EXTERNAL].median()
    tr = training[v2.EXTERNAL].fillna(medians).to_numpy()
    va = frame[v2.EXTERNAL].fillna(medians).to_numpy()
    maxtr, maxva = tr.max(axis=1), va.max(axis=1)
    rule = DecisionTreeClassifier(max_depth=1, random_state=42).fit(maxtr[:, None], training.feedback_label)
    result = {
        "prior_ap": float(y.mean()),
        "fraud_score_ap": float(average_precision_score(y, va[:, 0])),
        "max_external_ap": float(average_precision_score(y, maxva)),
        "mean_external_ap": float(average_precision_score(y, va.mean(axis=1))),
        "rule_threshold": float(rule.tree_.threshold[0]),
        "rule_confusion": confusion_matrix(y, rule.predict(maxva[:, None]), labels=[0, 1]).tolist(),
    }
    if pre is not None:
        z = pre.transform(training[v2.NAMES]); zv = pre.transform(frame[v2.NAMES])
        encoder = v2.make_encoder(z).fit(z)
        lr = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
        lr.fit(encoder.transform(z), training.feedback_label)
        result["logistic_ap"] = float(average_precision_score(y, lr.predict_proba(encoder.transform(zv))[:, 1]))
    return result


def operating_thresholds(y, predictions):
    """Dois pontos de operação em OOF, com recall e FPR explícitos.

    Usa o centro do intervalo entre observações para evitar depender da igualdade
    exata de uma probabilidade. Orçamentos são técnicos e provisórios.
    """
    y, p = np.asarray(y, int), np.asarray(predictions, float)
    unique = np.unique(p)
    candidates = np.r_[max(np.nextafter(0., 1.), unique[0] / 2),
                       (unique[:-1] + unique[1:]) / 2, unique[-1]]
    sorted_pos = np.sort(p[y == 1]); sorted_neg = np.sort(p[y == 0])
    recall = (len(sorted_pos) - np.searchsorted(sorted_pos, candidates, side="left")) / len(sorted_pos)
    fpr = (len(sorted_neg) - np.searchsorted(sorted_neg, candidates, side="left")) / len(sorted_neg)
    selected = []
    for target_recall, budget in [(.99, .005), (.95, .001)]:
        valid = (recall >= target_recall) & (fpr <= budget)
        if not valid.any():
            raise RuntimeError(f"Nenhum corte OOF atende recall {target_recall} com FPR <= {budget}")
        selected.append(float(candidates[valid].max()))
    low, high = selected
    if high <= low:
        high = min(1., float(np.nextafter(low, 1.)))
    return {"baixo": low, "alto": high, "model_version": v2.VERSION,
            "artifact_schema_version": 2,
            "selection_source": "development_out_of_fold",
            "operating_targets": {"review_recall": .99, "high_recall": .95,
                                   "review_max_fpr": .005, "high_max_fpr": .001,
                                   "business_approved": False},
            "input_policy": {"min_external_scores": 1, "without_scores": "abstain",
                             "degraded_with_missing_scores": True}}


def evaluate_partition(training, validation, label):
    pre, model, info = v2.fit_candidate(training)
    valid = v2.availability(validation) > 0
    q = v2.scores(pre, model, validation.loc[valid])
    record = {"label": label, "training": info,
              "metrics": evaluate(validation.loc[valid, "feedback_label"], q),
              "abstained": int((~valid).sum()),
              "baselines": reference_baselines(training, validation, pre)}
    log.info("%s AP=%.6f recall=%.4f", label, record["metrics"]["ap"], record["metrics"]["recall"])
    return pre, model, record, valid, q


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, default=Path("artifacts/model_v1"))
    parser.add_argument("--output", type=Path, default=Path("models_candidate_v2"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "docs/protocolo-modelo-v2.1.json"
    protocol = json.loads(protocol_path.read_text())
    dump(output / "protocol.json", {**protocol, "source_sha256": sha(protocol_path)})
    data, dataset = read_data(args.data)
    indexed = data.set_index("event_external_id", drop=False)
    original = json.loads((args.baseline_dir / "split_manifest.json").read_text())
    dev = indexed.loc[original["development"]["event_external_ids"]].reset_index(drop=True)
    holdout = indexed.loc[original["holdout"]["event_external_ids"]].reset_index(drop=True)
    demo = indexed.loc[original["demo"]["event_external_ids"]].reset_index(drop=True)
    report = {"model_version": v2.VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
              "dataset": dataset, "protocol_sha256": sha(protocol_path),
              "evaluation_status": "internal_retrospective_no_new_blind_holdout",
              "development_rows": len(dev), "historical_holdout_rows": len(holdout),
              "demo_reserved": len(demo), "folds": [], "temporal": [], "customer_group": []}
    report["provenance"] = {
        "python": sys.version, "neural_parameters": v2.PARAMS,
        "code_sha256": {name: sha(ROOT / name) for name in [
            "scripts/model_v2.py", "scripts/train_model_v2.py", "docs/protocolo-modelo-v2.1.json",
            "requirements-model.lock.txt"]},
    }
    report["provenance"]["baseline_split_sha256"] = sha(args.baseline_dir / "split_manifest.json")
    report["dataset"]["currency_code"] = {
        "observed_non_null": sorted(data.currency_code.dropna().unique().tolist()),
        "missing_labeled_period": int(data.currency_code.isna().sum()),
        "interpretation": "Somente BRL observado; inclusão atende ao contrato, sem validar outras moedas.",
    }
    oof = np.full(len(dev), np.nan)
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    for fold, (a, b) in enumerate(cv.split(dev, dev.feedback_label), 1):
        _, _, result, valid, q = evaluate_partition(dev.iloc[a], dev.iloc[b], f"stratified_{fold}")
        oof[b[valid]] = q
        report["folds"].append(result)
        dump(output / "metrics.json", report)
    covered = np.isfinite(oof)
    thresholds = operating_thresholds(dev.loc[covered, "feedback_label"], oof[covered])
    report["oof"] = evaluate(dev.loc[covered, "feedback_label"], oof[covered], thresholds["baixo"])
    report["oof_high_priority"] = evaluate(dev.loc[covered, "feedback_label"], oof[covered], thresholds["alto"])
    report["thresholds"] = thresholds
    for last, following in [(1, 2), (2, 3)]:
        a = dev.loc[dev.timestamp.dt.month <= last]; b = dev.loc[dev.timestamp.dt.month == following]
        _, _, result, _, _ = evaluate_partition(a, b, f"through_{last}_test_{following}")
        report["temporal"].append(result)
        dump(output / "metrics.json", report)
    cv_group = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=42)
    for fold, (a, b) in enumerate(cv_group.split(dev, dev.feedback_label, dev.customer_id.fillna("MISSING")), 1):
        _, _, result, _, _ = evaluate_partition(dev.iloc[a], dev.iloc[b], f"customer_group_{fold}")
        report["customer_group"].append(result)
        dump(output / "metrics.json", report)
    # Modelo final: nenhum rótulo de holdout ou demo entra neste ajuste.
    pre, model, fitting = v2.fit_candidate(dev)
    valid = v2.availability(holdout) > 0
    original_scores = v2.scores(pre, model, holdout.loc[valid])
    report["final_training"] = fitting
    report["historical_regression"] = evaluate(holdout.loc[valid, "feedback_label"], original_scores, thresholds["baixo"])
    report["historical_regression_high_priority"] = evaluate(holdout.loc[valid, "feedback_label"], original_scores, thresholds["alto"])
    report["historical_baselines"] = reference_baselines(dev, holdout, pre)
    report["outages"] = {}
    for mask in range(1, 8):
        frame = holdout.copy()
        omitted = []
        for j, name in enumerate(v2.EXTERNAL):
            if not mask & (1 << j):
                frame[name] = np.nan; omitted.append(name)
        eligible = v2.availability(frame) > 0
        predictions = v2.scores(pre, model, frame.loc[eligible])
        report["outages"][str(mask)] = {
            "available_mask": mask, "omitted": omitted, "eligible_rows": int(eligible.sum()),
            "abstained_rows": int((~eligible).sum()),
            "abstained_positives": int(frame.loc[~eligible, "feedback_label"].sum()),
            "total_positives": int(frame.feedback_label.sum()),
            "metrics": evaluate(frame.loc[eligible, "feedback_label"], predictions, thresholds["baixo"]),
            "calibration_certified_for_outage": False,
        }
        report["outages"][str(mask)]["capture_including_abstention_as_not_detected"] = (
            report["outages"][str(mask)]["metrics"]["confusion_matrix"][1][1] / int(frame.feedback_label.sum())
        )
    # Equivalência semântica para todos os registros: NaN e JSON null.
    differences = {}
    for column in v2.NAMES:
        nan = holdout[v2.NAMES].copy(); null = nan.copy()
        nan[column] = np.nan; null[column] = None
        differences[column] = float(np.max(abs(v2.scores(pre, model, nan)-v2.scores(pre, model, null))))
    report["null_equivalence_max_score_difference"] = differences
    served = holdout[v2.NAMES].astype(object).where(holdout[v2.NAMES].notna(), None)
    served["mcc"] = served.mcc.map(lambda x: None if x is None else str(x))
    served["amount"] = pd.to_numeric(served.amount, errors="coerce").fillna(0.)
    report["csv_json_full_parity_max_score_difference"] = float(np.max(abs(v2.scores(pre, model, served.loc[valid])-original_scores)))
    frac, mean = calibration_curve(holdout.loc[valid, "feedback_label"], original_scores, n_bins=10, strategy="quantile")
    report["calibration"] = {"method": "sigmoid_prefit_independent_natural_calibration",
        "historical_reliability": {"fraction_positive": frac.tolist(), "mean_prediction": mean.tolist()},
        "positive_count_final_calibration": fitting["calibration_positives"],
        "certified": False, "reason": "poucos positivos, dados sintéticos e validação retrospectiva"}
    fit_rows = indexed.loc[fitting["fit_event_ids"]]
    explanation = v2.build_explainer(pre, model, fit_rows)
    test_rows = pd.concat([dev.loc[dev.feedback_label.eq(1)].head(3), dev.loc[dev.feedback_label.eq(0)].head(3)])
    z = pre.transform(test_rows[v2.NAMES])
    values = explanation.shap_values(z, silent=True)
    base = float(explanation.expected_value)
    stability = []
    for seed in [11, 23, 37]:
        alternative = v2.build_explainer(pre, model, fit_rows, seed)
        other = alternative.shap_values(z, silent=True)
        # Medir importância substantiva nos positivos, em vez de ranquear ruído
        # de contribuições quase nulas de casos legítimos.
        similarities = []
        for before, after in zip(values[:3], other[:3]):
            a, b = set(np.argsort(-abs(before))[:3]), set(np.argsort(-abs(after))[:3])
            similarities.append(len(a & b) / len(a | b))
        stability.append({"seed": seed, "base_value": float(alternative.expected_value),
                          "max_abs_contribution_difference": float(np.max(abs(other-values))),
                          "mean_positive_top3_jaccard": float(np.mean(similarities))})
    report["explainability"] = {
        "background_size": len(explanation.background_event_ids_),
        "background_positives": int(indexed.loc[explanation.background_event_ids_, "feedback_label"].sum()),
        "class_weights": explanation.background_class_weights_, "base_value": base,
        "natural_fit_mean_score": float(v2.scores(pre, model, fit_rows).mean()),
        "max_additivity_error": float(np.max(abs(values.sum(axis=1)+base-model.predict_proba(z)[:, 1]))),
        "stability": stability, "runtime_custom_pickle_modules": [],
    }
    # Gates são verificados antes da publicação; falhas ficam registradas.
    checks = protocol["promotion_checks"]
    mean_ap = float(np.mean([f["metrics"]["ap"] for f in report["folds"]]))
    mean_baseline = float(np.mean([f["baselines"]["max_external_ap"] for f in report["folds"]]))
    regression = report["historical_regression"]
    two_available = [report["outages"][str(mask)]["metrics"]["recall"] for mask in [3, 5, 6]]
    report["promotion_gates"] = {
        "development_ap": mean_ap >= checks["development_mean_ap_minimum"],
        "baseline_parity": mean_baseline-mean_ap <= checks["development_max_ap_regret_vs_max_baseline"],
        "regression_ap": regression["ap"] >= checks["historical_regression_ap_minimum"],
        "regression_recall": regression["recall"] >= checks["historical_regression_recall_minimum"],
        "single_score_outage_recall": min(two_available) >= checks["historical_single_score_outage_recall_minimum"],
        "outage_fpr_budget": max(s["metrics"]["fpr"] for s in report["outages"].values()) <= .005,
        "null_equivalence": max(differences.values()) <= checks["null_representation_max_score_difference"],
        "csv_json_parity": report["csv_json_full_parity_max_score_difference"] <= 1e-12,
        "shap_additivity": report["explainability"]["max_additivity_error"] <= 1e-6,
        "shap_positive_top3_stability": min(s["mean_positive_top3_jaccard"] for s in stability) >= .8,
    }
    report["development_mean_ap"] = mean_ap
    report["development_mean_max_baseline_ap"] = mean_baseline
    report["candidate_passes_statistical_gates"] = all(report["promotion_gates"].values())
    dump(output / "metrics.json", report)
    joblib.dump(pre, output / "pipeline_v1.pkl")
    joblib.dump(model, output / "model_v1.pkl")
    joblib.dump(explanation, output / "explainer_v1.pkl")
    dump(output / "feature_names.json", v2.NAMES)
    dump(output / "thresholds.json", thresholds)
    dump(output / "split_manifest.json", {**original, "v2_final_fit": fitting["fit_event_ids"],
        "v2_final_calibration": fitting["calibration_event_ids"],
        "v2_background_event_ids": explanation.background_event_ids_})
    dump(output / "demo_cases.json", json.loads((args.baseline_dir / "demo_cases.json").read_text()))
    dump(output / "artifact_manifest.json", {
        "model_version": v2.VERSION, "artifact_schema_version": 2,
        "sha256": {name: sha(output / name) for name in ["model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
            "feature_names.json", "thresholds.json", "metrics.json", "protocol.json"]},
    })
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts/validate_artifacts.py"), "--models", str(output), "--samples", "4"], check=True)
    manifest = json.loads((output / "artifact_manifest.json").read_text())
    manifest["fresh_process_validation"] = True
    dump(output / "artifact_manifest.json", manifest)
    log.info("Candidata pronta: %s; gates=%s", output, report["promotion_gates"])
    print(json.dumps({"candidate": str(output), "gates": report["promotion_gates"],
                      "regression": regression, "thresholds": thresholds}, ensure_ascii=False, indent=2))
    return 0 if report["candidate_passes_statistical_gates"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
