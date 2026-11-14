"""
train_model.py — Pipeline completo de treinamento, avaliação e artefatos v1.

Uso:
    .venv/bin/python scripts/train_model.py \
        --data ./case_transacoes_cartoes_sintetico_900k.csv \
        --output models

Dependências (já no .venv):
    scikit-learn==1.5.2  shap==0.46  numpy==1.26  pandas==2.2  joblib==1.4.2

Decisões documentadas:
    D1  fraud_score/visa_fraud_score/mc_fraud_score incluídos como features
        (scores externos de bandeira — premissa de disponibilidade no scoring).
    D2  Apenas jan–mar/2025 têm feedback_label confirmado; usados para
        treino/avaliação. Demais meses só inferência.
    D7  6 fraudes reservadas antes de qualquer split (demo_cases.json).

Features v1 (exatamente o que DossierBuilder envia ao ScoringService):
    NUMÉRICAS  : amount, fraud_score, visa_fraud_score, mc_fraud_score, mcc
    CATEGÓRICAS: tipo_transacao, modalidade_transacao, pos_environment

    Total = 8 features (= 8 saídas do ColumnTransformer — 1 por coluna bruta).
    Sem OHE expandido; OrdinalEncoder mantém 1 coluna por feature categórica.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import operator
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np
import pandas as pd
import shap
import sklearn
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OrdinalEncoder, StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes do protocolo
# ---------------------------------------------------------------------------

NUMERIC_FEATURES = [
    "amount",
    "fraud_score",
    "visa_fraud_score",
    "mc_fraud_score",
    "mcc",
]
CATEGORICAL_FEATURES = [
    "tipo_transacao",
    "modalidade_transacao",
    "pos_environment",
]
FEATURE_NAMES: list[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES  # ordem canônica

TARGET = "feedback_label"
SEED = 42
N_DEMO = 6
CV_FOLDS = 3
HOLDOUT_RATIO = 0.20
OVERSAMPLE_RATIO = 0.10  # positivos / negativos no treino
THRESHOLD_LOW = 0.40
THRESHOLD_HIGH = 0.70
MODEL_VERSION = "v1.0.0"

MLP_PARAMS = dict(
    hidden_layer_sizes=(32, 16),
    max_iter=150,
    early_stopping=False,   # evita contaminação por duplicatas no split interno
    random_state=SEED,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def precision_recall_at_k(y_true: np.ndarray, scores: np.ndarray, k: int = 100):
    """Precision e recall nos top-k scores."""
    k = min(k, len(y_true))
    top_idx = np.argsort(-scores, kind="stable")[:k]
    pred = np.zeros(len(y_true), dtype=int)
    pred[top_idx] = 1
    tp = int((pred * y_true).sum())
    p = tp / k if k > 0 else 0.0
    r = tp / max(int(y_true.sum()), 1)
    return p, r


def evaluate_at_threshold(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict:
    pred = (proba >= threshold).astype(int)
    cm = confusion_matrix(y_true, pred, labels=[0, 1]).tolist()
    return {
        "threshold": threshold,
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "confusion_matrix": cm,
    }


def make_pipeline() -> Pipeline:
    """Cria pipeline pré-processamento + modelo sem fit."""
    num_transformer = Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ])
    cat_transformer = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="__missing__",
                                  keep_empty_features=True)),
        ("encoder", OrdinalEncoder(
            handle_unknown="use_encoded_value",
            unknown_value=-1,
            encoded_missing_value=-1,
        )),
        ("scaler", StandardScaler()),
    ])
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", num_transformer, NUMERIC_FEATURES),
            ("cat", cat_transformer, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
    model = MLPClassifier(**MLP_PARAMS)
    return Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", model),
    ])


# ---------------------------------------------------------------------------
# Carregamento e preparação de dados
# ---------------------------------------------------------------------------


def load_data(csv_path: pathlib.Path) -> pd.DataFrame:
    log.info("Carregando CSV: %s", csv_path)
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    required = FEATURE_NAMES + [TARGET, "event_external_id", "event_occurred_at"]
    missing = sorted(set(required) - set(header))
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {missing}")
    dtype_map: dict[str, Any] = {c: "object" for c in CATEGORICAL_FEATURES}
    df = pd.read_csv(
        csv_path,
        usecols=required,
        dtype=dtype_map,
        low_memory=False,
    )
    df.attrs["raw_rows"] = len(df)
    df.attrs["raw_columns"] = len(header)
    log.info("Dados brutos: %d linhas, %d colunas; lendo apenas %d colunas necessárias",
             len(df), len(header), len(required))
    return df


def prepare_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra e prepara o dataset:
    - Converte feedback_label para int (0/1).
    - Mantém apenas jan–mar/2025 (únicos meses com feedback confirmado — D2).
    - Remove linha com event_occurred_at inválido (timestamp nulo/fora do período).
    - Normaliza mcc como numérico (evita tipo misto no serviço).
    """
    audit = dict(df.attrs)
    df = df.copy()
    df["_source_row"] = np.arange(len(df))
    df[TARGET] = df[TARGET].astype("string").str.strip().str.lower().map(
        {"true": 1, "false": 0, "1": 1, "0": 0, "1.0": 1, "0.0": 0}
    )
    audit["missing_or_invalid_labels"] = int(df[TARGET].isna().sum())
    audit["raw_positives"] = int(df[TARGET].sum())

    # Remove linha com target nulo
    before = len(df)
    df = df.dropna(subset=[TARGET])
    df[TARGET] = df[TARGET].astype(int)
    log.info("Linhas com target nulo removidas: %d", before - len(df))

    # Filtro temporal: apenas jan–mar/2025
    df["_ts"] = pd.to_datetime(df["event_occurred_at"], format="mixed", errors="coerce", utc=True)
    audit["invalid_timestamps_labeled"] = int(df["_ts"].isna().sum())
    mask_valid = (
        df["_ts"].notna()
        & (df["_ts"].dt.year == 2025)
        & (df["_ts"].dt.month.between(1, 3))
    )
    before = len(df)
    df = df[mask_valid].copy()
    df.drop(columns=["_ts"], inplace=True)
    log.info("Filtro jan–mar/2025: %d → %d linhas", before, len(df))

    for col in NUMERIC_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)

    # Garante que categóricas são string/object (não categoria pandas)
    for col in CATEGORICAL_FEATURES:
        if col in df.columns:
            df[col] = df[col].astype(object).where(df[col].notna(), other=np.nan)

    # Preserva apenas colunas necessárias + identificador + target
    keep = FEATURE_NAMES + [TARGET, "event_external_id", "_source_row"]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].reset_index(drop=True)
    if df["event_external_id"].isna().any() or df["event_external_id"].duplicated().any():
        raise ValueError("event_external_id deve estar presente e ser único na partição avaliada")
    if df[TARGET].nunique() != 2:
        raise ValueError("O período jan–mar/2025 precisa conter as duas classes")
    df.attrs.update(audit)

    n_pos = int(df[TARGET].sum())
    n_neg = int((df[TARGET] == 0).sum())
    log.info("Dataset preparado: %d linhas | %d positivos | %d negativos",
             len(df), n_pos, n_neg)
    return df


# ---------------------------------------------------------------------------
# Reserva de casos demo (D7)
# ---------------------------------------------------------------------------


def reserve_demo_cases(
    df: pd.DataFrame,
    n: int = N_DEMO,
    seed: int = SEED,
    provided_ids: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reserva n positivos antes de qualquer split (D7).
    Retorna (df_rest, df_demo).
    Se provided_ids fornecidos, usa esses event_external_id.
    Caso contrário, seleção determinística via seed42.
    """
    positives = df[df[TARGET] == 1].copy()

    if provided_ids is not None:
        if len(provided_ids) != n or len(set(provided_ids)) != n:
            raise ValueError(f"Forneça exatamente {n} IDs únicos para a demonstração")
        demo_idx = df[df["event_external_id"].isin(provided_ids)].index.tolist()
        if len(demo_idx) != n or not df.loc[demo_idx, TARGET].eq(1).all():
            raise ValueError("Todos os IDs de demonstração devem ser fraudes do período avaliado")
    else:
        if len(positives) < n + CV_FOLDS + 2:
            raise ValueError("Fraudes insuficientes para reserva, holdout e validação cruzada")
        rng_state = np.random.RandomState(seed)
        demo_idx = positives.sample(n=n, random_state=rng_state).index.tolist()

    df_demo = df.loc[demo_idx].copy()
    df_rest = df.drop(index=demo_idx).reset_index(drop=True)
    log.info("Casos demo reservados: %d (IDs: %s)", len(df_demo),
             df_demo["event_external_id"].tolist() if "event_external_id" in df_demo.columns else "N/A")
    return df_rest, df_demo


# ---------------------------------------------------------------------------
# Holdout estratificado
# ---------------------------------------------------------------------------


def stratified_holdout(
    df: pd.DataFrame,
    holdout_ratio: float = HOLDOUT_RATIO,
    seed: int = SEED,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa holdout estratificado. Retorna (df_dev, df_holdout)."""
    from sklearn.model_selection import train_test_split
    df_dev, df_holdout = train_test_split(
        df,
        test_size=holdout_ratio,
        stratify=df[TARGET],
        random_state=seed,
    )
    df_dev = df_dev.reset_index(drop=True)
    df_holdout = df_holdout.reset_index(drop=True)
    log.info(
        "Holdout estratificado 80/20: dev=%d (pos=%d) | holdout=%d (pos=%d)",
        len(df_dev), int(df_dev[TARGET].sum()),
        len(df_holdout), int(df_holdout[TARGET].sum()),
    )
    return df_dev, df_holdout


# ---------------------------------------------------------------------------
# Oversample de positivos (só no treino)
# ---------------------------------------------------------------------------


def oversample_train(
    X: pd.DataFrame,
    y: pd.Series,
    ratio: float = OVERSAMPLE_RATIO,
    seed: int = SEED,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Duplica positivos no conjunto de treino até ratio = pos/neg.
    Pré-processamento DEVE ser fit no dataset original antes desta função.
    """
    if ratio <= 0 or not y.isin([0, 1]).all() or y.nunique() != 2:
        raise ValueError("Oversampling exige razão positiva e ambas as classes binárias")
    n_neg = int((y == 0).sum())
    n_pos_target = max(int(n_neg * ratio), int(y.sum()))
    n_pos_current = int(y.sum())
    n_add = n_pos_target - n_pos_current

    if n_add <= 0:
        return X, y

    pos_mask = y == 1
    rng = np.random.RandomState(seed)
    extra_idx = rng.choice(X[pos_mask].index, size=n_add, replace=True)
    X_extra = X.loc[extra_idx]
    y_extra = y.loc[extra_idx]

    X_out = pd.concat([X, X_extra], ignore_index=True)
    y_out = pd.concat([y, y_extra], ignore_index=True)
    log.debug("  Oversample: %d → %d positivos (ratio=%.3f)",
              n_pos_current, int(y_out.sum()), ratio)
    return X_out, y_out


# ---------------------------------------------------------------------------
# CV de desenvolvimento
# ---------------------------------------------------------------------------

def _fit_classifier(clf: MLPClassifier, X: np.ndarray, y: np.ndarray) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        clf.fit(X, y)
    clf.training_warnings_ = [str(item.message) for item in caught]
    for message in clf.training_warnings_:
        log.warning("Treinamento: %s", message)


def run_cv(df_dev: pd.DataFrame) -> dict:
    """
    CV 3-fold estratificada apenas em df_dev.
    Pipeline novo fit dentro de cada fold (nunca no holdout).
    Pré-processamento fit no treino original ANTES do oversample.
    """
    log.info("Iniciando CV %d-fold no conjunto de desenvolvimento...", CV_FOLDS)
    X = df_dev[FEATURE_NAMES]
    y = df_dev[TARGET]

    skf = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=SEED)

    fold_metrics = []
    for fold_i, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        X_tr_raw, y_tr = X.iloc[train_idx].copy(), y.iloc[train_idx].copy()
        X_val, y_val = X.iloc[val_idx].copy(), y.iloc[val_idx]

        # Pipeline novo a cada fold
        pipe = make_pipeline()

        # Fit do preprocessador no treino ORIGINAL (antes de oversample)
        # sklearn Pipeline: fit chama preprocessor.fit_transform + classifier.fit
        # Para oversample precisamos separar:
        preprocessor = pipe.named_steps["preprocessor"]
        preprocessor.fit(X_tr_raw, y_tr)
        X_tr_transformed = preprocessor.transform(X_tr_raw)

        # Oversample no espaço transformado (evitar leakage)
        X_tr_df = pd.DataFrame(X_tr_transformed,
                               index=X_tr_raw.index,
                               columns=FEATURE_NAMES)
        y_tr_copy = y_tr.copy()
        X_tr_os, y_tr_os = oversample_train(X_tr_df, y_tr_copy)

        # Treinar classificador com dados oversampled
        clf = pipe.named_steps["classifier"]
        _fit_classifier(clf, X_tr_os.values, y_tr_os.values)

        # Avaliar no fold de validação
        X_val_transformed = preprocessor.transform(X_val)
        proba_val = clf.predict_proba(X_val_transformed)[:, 1]

        ap = float(average_precision_score(y_val, proba_val))
        roc = float(roc_auc_score(y_val, proba_val))
        brier = float(brier_score_loss(y_val, proba_val))
        pr100 = precision_recall_at_k(y_val.values, proba_val, k=100)
        t040 = evaluate_at_threshold(y_val.values, proba_val, 0.40)
        t070 = evaluate_at_threshold(y_val.values, proba_val, 0.70)
        n_iter = int(getattr(clf, "n_iter_", -1))

        fold_m = {
            "fold": fold_i + 1,
            "n_train": len(X_tr_raw),
            "n_val": len(X_val),
            "n_pos_train": int(y_tr.sum()),
            "n_pos_val": int(y_val.sum()),
            "ap": ap,
            "roc_auc": roc,
            "brier": brier,
            "precision_at_100": pr100[0],
            "recall_at_100": pr100[1],
            "at_0.40": t040,
            "at_0.70": t070,
            "n_iter_convergence": n_iter,
            "convergence_warning": bool(clf.training_warnings_),
        }
        fold_metrics.append(fold_m)
        log.info(
            "  Fold %d | AP=%.4f | ROC-AUC=%.4f | Brier=%.4f | n_iter=%d",
            fold_i + 1, ap, roc, brier, n_iter,
        )

    # Médias
    aps = [m["ap"] for m in fold_metrics]
    rocs = [m["roc_auc"] for m in fold_metrics]
    briers = [m["brier"] for m in fold_metrics]
    log.info(
        "CV resumo — AP: %.4f ± %.4f | ROC-AUC: %.4f ± %.4f | Brier: %.4f ± %.4f",
        np.mean(aps), np.std(aps),
        np.mean(rocs), np.std(rocs),
        np.mean(briers), np.std(briers),
    )

    return {
        "folds": fold_metrics,
        "mean_ap": float(np.mean(aps)),
        "std_ap": float(np.std(aps)),
        "mean_roc_auc": float(np.mean(rocs)),
        "std_roc_auc": float(np.std(rocs)),
        "mean_brier": float(np.mean(briers)),
        "std_brier": float(np.std(briers)),
    }


# ---------------------------------------------------------------------------
# Modelo final — fit em desenvolvimento completo
# ---------------------------------------------------------------------------


def fit_final_model(
    df_dev: pd.DataFrame,
) -> tuple[ColumnTransformer, MLPClassifier]:
    """
    Treina modelo final em TODO df_dev (sem fold).
    Pré-processamento fit em df_dev original → oversample → fit classificador.
    Retorna (preprocessor_fitted, classifier_fitted).
    """
    log.info("Treinando modelo final em desenvolvimento completo (%d linhas)...", len(df_dev))
    X = df_dev[FEATURE_NAMES]
    y = df_dev[TARGET]

    pipe = make_pipeline()
    preprocessor = pipe.named_steps["preprocessor"]
    preprocessor.fit(X, y)
    X_transformed = preprocessor.transform(X)

    X_df = pd.DataFrame(X_transformed, index=X.index, columns=FEATURE_NAMES)
    X_os, y_os = oversample_train(X_df, y.copy())

    clf = pipe.named_steps["classifier"]
    _fit_classifier(clf, X_os.values, y_os.values)

    n_iter = getattr(clf, "n_iter_", -1)
    log.info("Modelo final treinado | n_iter=%d | convergiu=%s",
             n_iter, not clf.training_warnings_)
    return preprocessor, clf


# ---------------------------------------------------------------------------
# Avaliação no holdout (UMA VEZ)
# ---------------------------------------------------------------------------


def evaluate_holdout(
    preprocessor: ColumnTransformer,
    clf: MLPClassifier,
    df_holdout: pd.DataFrame,
) -> dict:
    """Avalia no holdout intocado. Chamado apenas uma vez."""
    log.info("Avaliando no holdout (%d linhas)...", len(df_holdout))
    X_h = df_holdout[FEATURE_NAMES]
    y_h = df_holdout[TARGET].values

    X_transformed = preprocessor.transform(X_h)
    proba = clf.predict_proba(X_transformed)[:, 1]

    ap = float(average_precision_score(y_h, proba))
    roc = float(roc_auc_score(y_h, proba))
    brier = float(brier_score_loss(y_h, proba))
    pr100 = precision_recall_at_k(y_h, proba, k=100)

    # Curva de calibração (10 bins)
    try:
        frac_pos, mean_pred = calibration_curve(y_h, proba, n_bins=10, strategy="uniform")
        cal_curve = {
            "fraction_of_positives": frac_pos.tolist(),
            "mean_predicted_value": mean_pred.tolist(),
        }
    except Exception:
        cal_curve = {}

    metrics = {
        "n_holdout": len(df_holdout),
        "n_pos_holdout": int(y_h.sum()),
        "ap": ap,
        "roc_auc": roc,
        "brier": brier,
        "precision_at_100": pr100[0],
        "recall_at_100": pr100[1],
        "at_0.40": evaluate_at_threshold(y_h, proba, 0.40),
        "at_0.70": evaluate_at_threshold(y_h, proba, 0.70),
        "calibration_curve": cal_curve,
    }
    log.info(
        "Holdout — AP=%.4f | ROC-AUC=%.4f | Brier=%.4f | Prec@100=%.3f | Rec@100=%.3f",
        ap, roc, brier, pr100[0], pr100[1],
    )
    return metrics


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def compute_baseline(df_dev: pd.DataFrame, df_holdout: pd.DataFrame) -> dict:
    """Baseline: prior constante + ranking fraud_score externo."""
    y_h = df_holdout[TARGET].values
    prior = float(df_dev[TARGET].mean())

    constant = np.full(len(y_h), prior)
    baselines: dict = {
        "prior_rate_dev": prior,
        "baseline_prior": {
            "ap": float(average_precision_score(y_h, constant)),
            "roc_auc": float(roc_auc_score(y_h, constant)),
            "brier": float(brier_score_loss(y_h, constant)),
        },
    }

    for col in ["fraud_score", "visa_fraud_score", "mc_fraud_score"]:
        # Imputação baseada só no desenvolvimento, sem consultar holdout.
        scores = df_holdout[col].fillna(df_dev[col].median()).values.astype(float)
        if scores.max() > 1.0:
            scores = scores / 999.0  # normaliza escala 0–999 → 0–1
        if scores.std() < 1e-9:
            baselines[f"baseline_{col}"] = {"note": "zero_variance"}
            continue
        ap = float(average_precision_score(y_h, scores))
        roc = float(roc_auc_score(y_h, scores))
        p100, r100 = precision_recall_at_k(y_h, scores, k=100)
        baselines[f"baseline_{col}"] = {
            "ap": ap, "roc_auc": roc,
            "precision_at_100": p100, "recall_at_100": r100,
            "note": "Ranking externo; escala dividida por 999 não implica calibração",
        }

    log.info("Baseline prior=%.5f | fraud_score AP=%s",
             prior,
             baselines.get("baseline_fraud_score", {}).get("ap", "N/A"))
    return baselines


# ---------------------------------------------------------------------------
# Construção do KernelExplainer portátil
# ---------------------------------------------------------------------------


def build_explainer(
    preprocessor: ColumnTransformer,
    clf: MLPClassifier,
    df_dev: pd.DataFrame,
    n_background: int = 32,
    seed: int = SEED,
) -> shap.KernelExplainer:
    """
    Constrói KernelExplainer portátil.

    Callable: Pipeline([('proba', FunctionTransformer(clf.predict_proba)),
                        ('positive', FunctionTransformer(operator.itemgetter((slice(None),1))))])

    Compatível com shap 0.46: shap_values(X) retorna array 2D (n, 8).
    O serviço chama explainer.shap_values(X)[0] → vetor 1D de 8 valores.

    Background: amostra do df_dev ORIGINAL (não oversampled, não holdout, não demo),
                transformado pelo preprocessor, até n_background linhas.
    """
    log.info("Construindo KernelExplainer (background=%d)...", n_background)

    X_dev = df_dev[FEATURE_NAMES]
    bg_sample = X_dev.sample(
        n=min(n_background, len(X_dev)),
        random_state=seed,
    )
    bg_transformed = preprocessor.transform(bg_sample)

    # Callable portátil: bound method + operador stdlib — sem classes custom
    predict_proba_fn = FunctionTransformer(func=clf.predict_proba)
    select_positive_fn = FunctionTransformer(func=operator.itemgetter((slice(None), 1)))

    proba_pipeline = Pipeline([
        ("proba", predict_proba_fn),
        ("positive", select_positive_fn),
    ])

    explainer = shap.KernelExplainer(
        model=proba_pipeline.transform,
        data=bg_transformed,
        link="identity",
        feature_names=FEATURE_NAMES,
    )
    explainer.background_event_ids_ = df_dev.loc[bg_sample.index, "event_external_id"].tolist()
    log.info("KernelExplainer criado (background shape: %s).", bg_transformed.shape)
    return explainer


# ---------------------------------------------------------------------------
# Salvamento de artefatos
# ---------------------------------------------------------------------------


def save_artifacts(
    output_dir: pathlib.Path,
    preprocessor: ColumnTransformer,
    clf: MLPClassifier,
    explainer: shap.KernelExplainer,
    metrics_all: dict,
    demo_df: pd.DataFrame,
    dataset_path: pathlib.Path,
    split_manifest: dict | None = None,
) -> None:
    """Valida em staging e só depois publica o diretório completo.

    Em reexecuções, preserva a versão anterior em um backup recuperável.
    Execute com o consumidor parado e reinicie-o depois da publicação.
    """
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-",
                                           dir=output_dir.parent))
    log.info("Salvando artefatos em staging: %s", staging)

    try:
        if output_dir.exists():
            shutil.copytree(output_dir, staging, dirs_exist_ok=True)
        # 1. pipeline_v1.pkl  (apenas preprocessor = ColumnTransformer)
        joblib.dump(preprocessor, staging / "pipeline_v1.pkl")

        # 2. model_v1.pkl
        joblib.dump(clf, staging / "model_v1.pkl")

        # 3. explainer_v1.pkl
        joblib.dump(explainer, staging / "explainer_v1.pkl")

        # 4. feature_names.json
        (staging / "feature_names.json").write_text(
            json.dumps(FEATURE_NAMES, indent=2), encoding="utf-8"
        )

        # 5. thresholds.json
        (staging / "thresholds.json").write_text(
            json.dumps({
                "baixo": THRESHOLD_LOW,
                "alto": THRESHOLD_HIGH,
                "model_version": MODEL_VERSION,
            }, indent=2),
            encoding="utf-8",
        )

        # metrics.json
        (staging / "metrics.json").write_text(
            json.dumps(metrics_all, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        # demo_cases.json
        demo_ids = demo_df["event_external_id"].tolist() if "event_external_id" in demo_df.columns else []
        demo_payload = {
            "description": "6 fraudes reservadas antes dos splits (D7). Não usadas em treino/calibração/background.",
            "seed": SEED,
            "n": N_DEMO,
            "event_external_ids": demo_ids,
            "source_rows": demo_df["_source_row"].tolist() if "_source_row" in demo_df else demo_df.index.tolist(),
            "selection": "IDs fornecidos ou amostra determinística antes dos splits; ver protocolo",
        }
        (staging / "demo_cases.json").write_text(
            json.dumps(demo_payload, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        if split_manifest is not None:
            (staging / "split_manifest.json").write_text(
                json.dumps(split_manifest, indent=2, allow_nan=False), encoding="utf-8"
            )
        (staging / "model_card.md").write_text(make_model_card(metrics_all), encoding="utf-8")
        # Sem depender de classes/funções do processo de treinamento.
        subprocess.run(
            [sys.executable, "-I", str(pathlib.Path(__file__).with_name("validate_artifacts.py").resolve()),
             "--models", str(staging), "--samples", "3"], check=True,
        )
        hashes = {name: sha256_file(staging / name) for name in (
            "model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
            "feature_names.json", "thresholds.json", "metrics.json", "model_card.md",
        )}
        (staging / "artifact_manifest.json").write_text(
            json.dumps({"model_version": MODEL_VERSION, "sha256": hashes,
                        "fresh_process_validation": True}, indent=2), encoding="utf-8"
        )
        backup = None
        if output_dir.exists():
            backup = pathlib.Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-backup-",
                                                  dir=output_dir.parent))
            backup.rmdir()  # somente o diretório temporário vazio recém-criado
            os.replace(output_dir, backup)
        try:
            os.replace(staging, output_dir)
        except BaseException:
            if backup is not None and not output_dir.exists():
                os.replace(backup, output_dir)
            raise
        if backup is not None:
            log.info("Versão anterior preservada em: %s", backup)

        log.info("Artefatos publicados em: %s", output_dir)

    except Exception:
        log.error("Falha na publicação; artefatos anteriores preservados")
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def make_model_card(metrics: dict) -> str:
    d, h, cv = metrics["dataset"], metrics["holdout"], metrics["cv"]
    baseline = metrics["baseline"]
    low, high = h["at_0.40"], h["at_0.70"]
    return f'''# Modelo de risco de fraude — {MODEL_VERSION}

Rede neural `MLPClassifier`, duas camadas ocultas de 32 e 16 neurônios.
Treinada no CSV do case adicionado pelo usuário; nenhum dado foi fabricado para o treino.
Score estimado em [0,1] para apoio à investigação, com decisão final humana.

## Dados e avaliação

- CSV: {d['n_total']:,} registros, {d['n_columns']} colunas; SHA-256 `{d['sha256']}`.
- Apenas jan–mar/2025: {d['n_eval_period']:,} registros e {d['n_pos_eval_period']} fraudes.
- Demonstração: {d['n_demo_reserved']} fraudes separadas antes de qualquer split.
- Desenvolvimento: {d['n_development']:,} registros ({d['n_pos_development']} fraudes).
- Holdout estratificado: {d['n_holdout']:,} registros ({d['n_pos_holdout']} fraudes).
- CV de 3 folds só no desenvolvimento: AP {cv.get('mean_ap', 0):.6f} ± {cv.get('std_ap', 0):.6f}.
- Pipeline reajustado dentro de cada fold; positivos duplicados só no treino, após fit
  do pré-processador nos dados originais. Oversampling: razão positivo/negativo 0,10.
- Modelo entregue ajustado apenas no desenvolvimento. Holdout não usado para seleção,
  treinamento, background SHAP ou definição de limiares. Seed 42.
- IDs e linhas de origem em `split_manifest.json`; reserva em `demo_cases.json`.

| Métrica no holdout | Rede neural | Referência |
|---|---:|---:|
| Average precision (AP, resumo da curva PR) | {h['ap']:.6f} | prior: {baseline['baseline_prior']['ap']:.6f} |
| AP | {h['ap']:.6f} | ranking `fraud_score`: {baseline['baseline_fraud_score']['ap']:.6f} |
| ROC-AUC | {h['roc_auc']:.6f} | ranking `fraud_score`: {baseline['baseline_fraud_score']['roc_auc']:.6f} |
| Precision@100 | {h['precision_at_100']:.4f} | ranking `fraud_score`: {baseline['baseline_fraud_score']['precision_at_100']:.4f} |
| Recall@100 | {h['recall_at_100']:.4f} | ranking `fraud_score`: {baseline['baseline_fraud_score']['recall_at_100']:.4f} |
| Brier | {h['brier']:.6f} | prior: {baseline['baseline_prior']['brier']:.6f} |

| Corte | Precisão | Recall | F1 | Matriz [[TN, FP], [FN, TP]] |
|---|---:|---:|---:|---|
| 0,40 | {low['precision']:.4f} | {low['recall']:.4f} | {low['f1']:.4f} | {low['confusion_matrix']} |
| 0,70 | {high['precision']:.4f} | {high['recall']:.4f} | {high['f1']:.4f} | {high['confusion_matrix']} |

Curva de calibração, métricas por fold, versões e convergência em `metrics.json`.
Os limiares 0,40 e 0,70 são os contratuais; não foram otimizados no holdout.

## Integração e explicabilidade

Oito entradas, na mesma ordem da saída do pipeline: `{', '.join(FEATURE_NAMES)}`.
São exatamente os campos que o DossierBuilder atual passa ao ScoringService.
Campos de velocidade, IP e dados do cartão não são usados nesta versão porque
o fluxo atual do dossiê não os envia. Nenhum alvo, resultado posterior ou ID entra
como feature. `mcc` é tratado numericamente; a distância entre códigos não deve
ser interpretada como relação semântica entre atividades.

Numéricas: mediana + StandardScaler. Categorias: imputação + OrdinalEncoder
(categorias novas codificadas como -1) + StandardScaler. A codificação ordinal
preserva uma coluna por entrada, permitindo usar o serviço sem alterações.
Ela impõe uma ordenação artificial às categorias, uma limitação desta versão.

Kernel SHAP explica a saída escalar da classe 1, em unidades de probabilidade
estimada, com link identity. Background: até 32 registros do desenvolvimento
original, sem oversampling. Mesmos pesos e pré-processamento da predição.
Top-5 por valor absoluto; neutro se |SHAP| < 0,01; positivo aumenta e negativo
reduz o score. A soma de TODAS as contribuições e o valor-base reconstrói o score;
os cinco fatores exibidos isoladamente podem não somar a diferença total.
Contribuição associativa ao modelo, sem interpretação causal.

Os cinco arquivos requeridos estão em `models/`, carregados por joblib/JSON.
Não dependem de funções do script de treino nem de classes personalizadas.
Use Python 3.12 e as versões de `requirements-model.txt` na inferência.
O serviço deve ser reiniciado após copiar os arquivos, porque mantém carga em cache.

## Limitações

Dados 100% sintéticos e somente {d['n_pos_holdout']} fraudes no holdout.
A partição é aleatória estratificada dentro de jan–mar, conforme D2, e não um
teste temporal futuro nem separado por cliente/cartão. Entidades podem se repetir
entre partições; não há comprovação de generalização a novos clientes ou eventos.
Meses posteriores e timestamps inválidos não têm avaliação de qualidade.
Oversampling altera a prevalência aprendida; o score não é uma probabilidade
calibrada certificada. Brier e curva de calibração documentam essa limitação.
Scores externos `fraud_score`, `visa_fraud_score`, `mc_fraud_score` estão incluídos
por D1: pressupõe-se disponibilidade no momento da autorização, a validar com o
Sicredi. A performance em dados bancários reais permanece não validada.

## Reprodução

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-model.txt
OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/train_model.py --data case_transacoes_cartoes_sintetico_900k.csv --output models
.venv/bin/python scripts/validate_artifacts.py --models models
```

Referências técnicas: [KernelExplainer](https://shap.readthedocs.io/en/stable/generated/shap.KernelExplainer.html),
[persistência scikit-learn](https://scikit-learn.org/1.5/model_persistence.html).
'''


# ---------------------------------------------------------------------------
# Construção de metrics.json completo
# ---------------------------------------------------------------------------


def build_metrics_json(
    cv_results: dict,
    holdout_metrics: dict,
    baseline: dict,
    df_full: pd.DataFrame,
    df_dev: pd.DataFrame,
    df_holdout: pd.DataFrame,
    df_demo: pd.DataFrame,
    dataset_path: pathlib.Path,
    clf: MLPClassifier,
) -> dict:
    return {
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "dataset": {
            "path": str(dataset_path),
            "sha256": sha256_file(dataset_path),
            "n_total": int(df_full.attrs.get("raw_rows", len(df_full))),
            "n_columns": int(df_full.attrs.get("raw_columns", len(df_full.columns))),
            "n_eval_period": len(df_dev) + len(df_holdout) + len(df_demo),
            "n_pos_eval_period": int(df_full[TARGET].sum()),
            "n_demo_reserved": len(df_demo),
            "n_development": len(df_dev),
            "n_holdout": len(df_holdout),
            "n_pos_development": int(df_dev[TARGET].sum()),
            "n_pos_holdout": int(df_holdout[TARGET].sum()),
            "n_pos_demo": int(df_demo[TARGET].sum()),
            "exclusions_audit": dict(df_full.attrs),
        },
        "seeds": {"global": SEED, "cv_shuffle": SEED, "oversample": SEED, "background": SEED},
        "protocol": {
            "eval_period": "2025-01 to 2025-03",
            "holdout_ratio": HOLDOUT_RATIO,
            "cv_folds": CV_FOLDS,
            "oversample_ratio": OVERSAMPLE_RATIO,
            "ranking_k": 100,
            "holdout_used_for_tuning": False,
            "split_type": "random_stratified_within_2025_Q1_not_future_temporal",
            "mlp_params": MLP_PARAMS,
        },
        "features": {
            "names": FEATURE_NAMES,
            "numeric": NUMERIC_FEATURES,
            "categorical": CATEGORICAL_FEATURES,
        },
        "library_versions": {
            "sklearn": sklearn.__version__,
            "shap": shap.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "joblib": joblib.__version__,
        },
        "convergence": {
            "n_iter_final": int(getattr(clf, "n_iter_", -1)),
            "max_iter": MLP_PARAMS["max_iter"],
            "warnings": getattr(clf, "training_warnings_", []),
            "loss_curve_last5": [round(v, 6) for v in (clf.loss_curve_ or [])[-5:]]
            if hasattr(clf, "loss_curve_") and clf.loss_curve_ else [],
        },
        "baseline": baseline,
        "cv": cv_results,
        "holdout": holdout_metrics,
        "thresholds": {
            "baixo": THRESHOLD_LOW,
            "alto": THRESHOLD_HIGH,
            "note": "Limiares contratuais fixos (docs/arquitetura.md 13.1). Não calibrados.",
        },
        "disclaimer": (
            "Dados sintéticos gerados para fins educacionais. "
            "Métricas NÃO refletem desempenho em produção. "
            "fraud_score/visa_fraud_score/mc_fraud_score são features com premissa "
            "de disponibilidade no momento de autorização (D1) — validar em produção."
        ),
    }


# ---------------------------------------------------------------------------
# CLI principal
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Treina o modelo v1 de detecção de fraude e salva artefatos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--data", required=True, type=pathlib.Path,
                   help="Caminho do CSV de transações")
    p.add_argument("--output", default="models", type=pathlib.Path,
                   help="Diretório de saída dos artefatos (padrão: models)")
    p.add_argument("--demo-ids", nargs="*", default=None,
                   help="event_external_id dos 6 casos demo (opcional; se omitido usa seed42)")
    p.add_argument("--background-size", type=int, default=32,
                   help="Linhas do background do KernelExplainer (padrão: 32)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.data.exists():
        log.error("Arquivo não encontrado: %s", args.data)
        return 1
    if args.background_size < 1:
        raise ValueError("--background-size deve ser positivo")

    log.info("=" * 60)
    log.info("train_model.py  |  modelo=%s  |  seed=%d", MODEL_VERSION, SEED)
    log.info("=" * 60)

    # 1. Carregamento e preparação
    df_raw = load_data(args.data)
    df = prepare_dataset(df_raw)
    del df_raw  # libera memória

    # 2. Reserva demo (D7)
    df_rest, df_demo = reserve_demo_cases(df, provided_ids=args.demo_ids)

    # 3. Holdout estratificado 80/20
    df_dev, df_holdout = stratified_holdout(df_rest)

    split_manifest = {
        "seed": SEED,
        "source_row_definition": "Índice zero-based da linha de dados no CSV, sem o cabeçalho",
        "demo_selection": "provided_ids" if args.demo_ids is not None else "deterministic_random_seed_42",
    }
    for name, part in [("development", df_dev), ("holdout", df_holdout), ("demo", df_demo)]:
        split_manifest[name] = {
            "event_external_ids": part["event_external_id"].tolist(),
            "source_rows": part["_source_row"].astype(int).tolist(),
        }
    identifiers = [set(split_manifest[name]["event_external_ids"])
                   for name in ("development", "holdout", "demo")]
    if any(identifiers[i] & identifiers[j] for i in range(3) for j in range(i)):
        raise ValueError("Sobreposição indevida entre demonstração, desenvolvimento e holdout")

    # 4. CV de desenvolvimento
    cv_results = run_cv(df_dev)

    # 5. Modelo final em desenvolvimento completo
    preprocessor, clf = fit_final_model(df_dev)

    # 6. Avaliação holdout (UMA VEZ)
    holdout_metrics = evaluate_holdout(preprocessor, clf, df_holdout)

    # 7. Baseline
    baseline = compute_baseline(df_dev, df_holdout)

    # 8. KernelExplainer
    explainer = build_explainer(preprocessor, clf, df_dev, n_background=args.background_size)
    split_manifest["background_event_ids"] = explainer.background_event_ids_
    if not set(explainer.background_event_ids_) <= identifiers[0]:
        raise ValueError("Background SHAP fora do desenvolvimento")

    # 9. Métricas consolidadas
    metrics_all = build_metrics_json(
        cv_results=cv_results,
        holdout_metrics=holdout_metrics,
        baseline=baseline,
        df_full=df,
        df_dev=df_dev,
        df_holdout=df_holdout,
        df_demo=df_demo,
        dataset_path=args.data,
        clf=clf,
    )

    metrics_all["explainability"] = {
        "method": "KernelExplainer", "output": "positive_class_probability",
        "link": "identity", "background_size": len(explainer.background_event_ids_),
        "background_source": "development_original_not_oversampled",
        "custom_pickle_modules": [],
    }
    # 10. Salvar e validar os cinco artefatos como uma entrega completa.
    save_artifacts(args.output, preprocessor, clf, explainer, metrics_all, df_demo,
                   args.data, split_manifest=split_manifest)

    log.info("=" * 60)
    log.info("Treinamento concluído. Artefatos em: %s", args.output)
    log.info("  AP (holdout) : %.4f", holdout_metrics["ap"])
    log.info("  ROC-AUC      : %.4f", holdout_metrics["roc_auc"])
    log.info("  Brier        : %.4f", holdout_metrics["brier"])
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
