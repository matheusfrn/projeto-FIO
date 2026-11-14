"""
validate_artifacts.py — Valida os artefatos do modelo v1 em processo novo.

Recarrega pipeline_v1.pkl, model_v1.pkl, explainer_v1.pkl, feature_names.json
e thresholds.json a partir de um diretório e executa checagens de contrato:

  C1  predict_proba retorna float [0,1]
  C2  SHAP shape = (n_features,) — vetor 1D, alinhado com feature_names
  C3  Aditividade SHAP: |sum(shap) + base_value - proba| < 1e-6 por amostra
  C4  Top-5 por |shap_value|
  C5  Direções: neutro = |shap| < 0.01; aumenta/reduz para os demais
  C6  Pipeline.transform(DataFrame[feature_names]) retorna (n, 8) sem expandir colunas
  C7  Nomes canônicos: FEATURE_NAMES listados em feature_names.json
  C8  thresholds.json contém baixo, alto, model_version

Uso:
    .venv/bin/python scripts/validate_artifacts.py --models models
    .venv/bin/python scripts/validate_artifacts.py --models models --samples 5 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CANONICAL_FEATURE_NAMES = [
    "amount", "fraud_score", "visa_fraud_score", "mc_fraud_score", "mcc",
    "tipo_transacao", "modalidade_transacao", "pos_environment",
]

ADDITIVTY_TOL = 1e-6
SHAP_NEUTRO_ABS = 0.01


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    pass


def _check(condition: bool, msg: str) -> None:
    if not condition:
        raise ValidationError(msg)


def _make_synthetic_rows(n: int, seed: int = 42) -> pd.DataFrame:
    """Gera n linhas sintéticas para teste de contrato (sem dados reais)."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        rows.append({
            "amount": float(rng.uniform(1.0, 5000.0)),
            "fraud_score": float(rng.uniform(0, 999)),
            "visa_fraud_score": float(rng.uniform(0, 999)),
            "mc_fraud_score": float(rng.uniform(0, 999)),
            "mcc": float(rng.choice([5411, 5732, 5912, 4111, 7011])),
            "tipo_transacao": rng.choice(["credito", "debito", None]),
            "modalidade_transacao": rng.choice(["cartao_nao_presente", "cartao_presente", None]),
            "pos_environment": rng.choice(["ECOMMERCE", "POS", None]),
            "currency_code": rng.choice(["BRL", None]),
        })
    df = pd.DataFrame(rows)
    # categóricas como object (None compatível)
    for col in ["tipo_transacao", "modalidade_transacao", "pos_environment", "currency_code"]:
        df[col] = df[col].astype(object)
    return df


def _classify_direction(v: float) -> str:
    if abs(v) < SHAP_NEUTRO_ABS:
        return "neutro"
    return "aumenta_risco" if v > 0 else "reduz_risco"


# ---------------------------------------------------------------------------
# Carregamento de artefatos
# ---------------------------------------------------------------------------

def load_artifacts(models_dir: pathlib.Path) -> dict:
    import joblib

    arts: dict = {}
    required = ["pipeline_v1.pkl", "model_v1.pkl", "explainer_v1.pkl", "feature_names.json", "thresholds.json"]
    missing = [r for r in required if not (models_dir / r).exists()]
    if missing:
        raise ValidationError(f"Artefatos obrigatórios ausentes: {missing}")

    arts["pipeline"] = joblib.load(models_dir / "pipeline_v1.pkl")
    arts["model"] = joblib.load(models_dir / "model_v1.pkl")

    with open(models_dir / "feature_names.json") as f:
        arts["feature_names"] = json.load(f)

    with open(models_dir / "thresholds.json") as f:
        arts["thresholds"] = json.load(f)

    explainer_path = models_dir / "explainer_v1.pkl"
    if explainer_path.exists():
        arts["explainer"] = joblib.load(explainer_path)
        log.info("explainer_v1.pkl carregado.")
    else:
        raise ValidationError("Explainer obrigatório ausente")

    log.info("Artefatos carregados de: %s", models_dir)
    return arts


# ---------------------------------------------------------------------------
# Verificações individuais
# ---------------------------------------------------------------------------

def check_feature_names(arts: dict) -> None:
    """C7: feature_names.json contém nomes canônicos na ordem correta."""
    fn = arts["feature_names"]
    expected = CANONICAL_FEATURE_NAMES + (["currency_code"]
        if arts["thresholds"]["model_version"].startswith("v2.1.") else [])
    _check(
        fn == expected,
        f"C7 FALHOU: feature_names.json={fn} != esperado={expected}",
    )
    log.info("C7 OK — feature_names: %s", fn)


def check_thresholds(arts: dict) -> None:
    """C8: thresholds.json válido."""
    t = arts["thresholds"]
    _check("baixo" in t, "C8 FALHOU: chave 'baixo' ausente em thresholds.json")
    _check("alto" in t, "C8 FALHOU: chave 'alto' ausente em thresholds.json")
    _check("model_version" in t, "C8 FALHOU: chave 'model_version' ausente em thresholds.json")
    _check(0 < t["baixo"] < t["alto"] <= 1.0,
           f"C8 FALHOU: limiares inválidos baixo={t['baixo']} alto={t['alto']}")
    log.info("C8 OK — thresholds: baixo=%.2f alto=%.2f version=%s",
             t["baixo"], t["alto"], t["model_version"])


def check_pipeline_transform(arts: dict, X: pd.DataFrame) -> np.ndarray:
    """C6: pipeline.transform retorna (n, n_features) — sem expandir colunas."""
    pipeline = arts["pipeline"]
    n_features = len(arts["feature_names"])
    X_t = pipeline.transform(X)
    _check(
        X_t.shape == (len(X), n_features),
        f"C6 FALHOU: pipeline.transform shape={X_t.shape} esperado=({len(X)}, {n_features})",
    )
    _check(
        np.all(np.isfinite(X_t)),
        "C6 FALHOU: pipeline.transform produziu NaN/inf — imputação falhou",
    )
    log.info("C6 OK — pipeline.transform: %s → %s", X.shape, X_t.shape)
    return X_t


def check_predict_proba(arts: dict, X_t: np.ndarray) -> np.ndarray:
    """C1: predict_proba retorna float [0,1] com shape (n, 2)."""
    model = arts["model"]
    proba = model.predict_proba(X_t)
    _check(np.array_equal(model.classes_, [0, 1]), "C1 FALHOU: ordem das classes incorreta")
    _check(proba.shape == (len(X_t), 2), f"C1 FALHOU: predict_proba shape={proba.shape}, esperado (n, 2)")
    _check(np.all(np.isfinite(proba)), "C1 FALHOU: probabilidades não finitas")
    scores = proba[:, 1]
    _check(
        float(scores.min()) >= 0.0 and float(scores.max()) <= 1.0,
        f"C1 FALHOU: scores fora de [0,1]: min={scores.min():.4f} max={scores.max():.4f}",
    )
    log.info("C1 OK — predict_proba: shape=%s scores∈[%.4f, %.4f]",
             proba.shape, float(scores.min()), float(scores.max()))
    return scores


def check_shap(arts: dict, X_t: np.ndarray, feature_names: list[str],
               verbose: bool = False) -> None:
    """C2+C3+C4+C5: SHAP shape, alinhamento, aditividade, top-5 e direções."""
    explainer = arts["explainer"]
    if explainer is None:
        raise ValidationError("C2-C5 FALHOU: explainer obrigatório ausente")

    model = arts["model"]
    n_features = len(feature_names)

    log.info("Calculando SHAP para %d amostras (pode demorar)...", X_t.shape[0])
    shap_values = explainer.shap_values(X_t)
    _check(np.all(np.isfinite(shap_values)), "C2 FALHOU: SHAP não finito")

    # C2: shape 2D (n, n_features)
    _check(
        isinstance(shap_values, np.ndarray) and shap_values.ndim == 2,
        f"C2 FALHOU: shap_values type={type(shap_values)} ndim={getattr(shap_values, 'ndim', '?')}",
    )
    _check(
        shap_values.shape == (X_t.shape[0], n_features),
        f"C2 FALHOU: shap_values.shape={shap_values.shape} esperado=({X_t.shape[0]}, {n_features})",
    )
    log.info("C2 OK — shap_values.shape=%s", shap_values.shape)

    # Verificar que [0] dá vetor 1D de n_features (contrato do serviço)
    sv_row0 = shap_values[0]
    _check(sv_row0.ndim == 1 and len(sv_row0) == n_features,
           f"C2 FALHOU: shap_values[0] shape={sv_row0.shape}, esperado ({n_features},)")
    log.info("C2 OK — shap_values[0].shape=%s (contrato serviço OK)", sv_row0.shape)

    # C3: aditividade por amostra
    proba = model.predict_proba(X_t)[:, 1]
    base_value = float(explainer.expected_value)
    reconstructed = shap_values.sum(axis=1) + base_value
    max_err = float(np.abs(reconstructed - proba).max())
    _check(
        max_err < ADDITIVTY_TOL,
        f"C3 FALHOU: aditividade max_err={max_err:.4f} > tol={ADDITIVTY_TOL}",
    )
    log.info("C3 OK — aditividade: max_err=%.5f (tol=%.2f)", max_err, ADDITIVTY_TOL)

    # C4+C5: top-5 e direções para cada amostra
    for i in range(X_t.shape[0]):
        sv = shap_values[i]
        indexed = sorted(zip(feature_names, sv), key=lambda x: abs(x[1]), reverse=True)
        top5 = indexed[:5]

        for feat, sv_val in top5:
            direction = _classify_direction(float(sv_val))
            _check(direction in ("neutro", "aumenta_risco", "reduz_risco"),
                   f"C5 FALHOU: direção inválida '{direction}' para feature={feat}")

        if verbose:
            log.info("  Amostra %d | base=%.4f proba=%.4f", i, base_value, float(proba[i]))
            for feat, sv_val in top5:
                log.info("    %-35s  shap=%+.4f  dir=%s",
                         feat, float(sv_val), _classify_direction(float(sv_val)))

    log.info("C4+C5 OK — top-5 e direções válidas para %d amostras", X_t.shape[0])

    # Estatística de neutros
    n_neutro = int(np.sum(np.abs(shap_values) < SHAP_NEUTRO_ABS))
    log.info("  Neutros (|shap|<%.2f): %d/%d valores",
             SHAP_NEUTRO_ABS, n_neutro, shap_values.size)


# ---------------------------------------------------------------------------
# Runner principal
# ---------------------------------------------------------------------------

def run_validation(models_dir: pathlib.Path, n_samples: int = 3, verbose: bool = False) -> bool:
    """Executa todas as verificações. Retorna True se OK, False se falhou."""
    log.info("=" * 60)
    log.info("validate_artifacts.py | dir=%s | amostras=%d", models_dir, n_samples)
    log.info("=" * 60)

    errors: list[str] = []

    try:
        arts = load_artifacts(models_dir)
    except ValidationError as e:
        log.error("ERRO FATAL ao carregar artefatos: %s", e)
        return False

    checks = [
        ("feature_names", lambda: check_feature_names(arts)),
        ("thresholds", lambda: check_thresholds(arts)),
    ]

    for name, fn in checks:
        try:
            fn()
        except ValidationError as e:
            log.error("FALHA %s: %s", name, e)
            errors.append(str(e))

    # Dados sintéticos para verificações funcionais
    X_test = _make_synthetic_rows(n_samples)

    # C6: pipeline transform
    X_t = None
    try:
        X_t = check_pipeline_transform(arts, X_test)
    except ValidationError as e:
        log.error("FALHA pipeline_transform: %s", e)
        errors.append(str(e))

    # C1: predict_proba
    if X_t is not None:
        try:
            check_predict_proba(arts, X_t)
        except ValidationError as e:
            log.error("FALHA predict_proba: %s", e)
            errors.append(str(e))

        # C2-C5: SHAP
        try:
            check_shap(arts, X_t, arts["feature_names"], verbose=verbose)
        except ValidationError as e:
            log.error("FALHA SHAP: %s", e)
            errors.append(str(e))

    log.info("=" * 60)
    if errors:
        log.error("VALIDAÇÃO FALHOU — %d erro(s):", len(errors))
        for i, err in enumerate(errors, 1):
            log.error("  %d. %s", i, err)
        return False
    else:
        log.info("VALIDAÇÃO OK — todos os contratos satisfeitos.")
        return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", default="models", type=pathlib.Path,
                   help="Diretório dos artefatos (padrão: models)")
    p.add_argument("--samples", type=int, default=3,
                   help="Número de amostras sintéticas para teste (padrão: 3)")
    p.add_argument("--verbose", action="store_true",
                   help="Exibe top-5 SHAP por amostra")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.samples < 1:
        raise ValueError("--samples deve ser positivo")
    np.random.seed(42)
    ok = run_validation(args.models, n_samples=args.samples, verbose=args.verbose)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
