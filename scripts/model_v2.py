"""Construção da v2; nenhum callable deste módulo é gravado nos pickles.

O pipeline externo preserva nove slots. A representação nominal, indicadores
de ausência e rede neural ficam dentro do estimador, permitindo que SHAP
continue explicando nove features originais no serviço existente.
"""
from __future__ import annotations

import functools
import logging
import operator
import warnings

import numpy as np
import pandas as pd
import shap
from shap.utils._legacy import DenseData
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, OrdinalEncoder, StandardScaler

NAMES = ["amount", "fraud_score", "visa_fraud_score", "mc_fraud_score", "mcc",
         "tipo_transacao", "modalidade_transacao", "pos_environment", "currency_code"]
EXTERNAL = NAMES[1:4]
VERSION = "v2.1.0"
PARAMS = dict(hidden_layer_sizes=(16,), max_iter=250, alpha=0.001,
              early_stopping=False, random_state=42, tol=1e-5, n_iter_no_change=20)
log = logging.getLogger(__name__)


def numeric_cast() -> FunctionTransformer:
    return FunctionTransformer(np.asarray, kw_args={"dtype": "float64"},
                               feature_names_out="one-to-one")


def make_preprocessor() -> ColumnTransformer:
    # NaN é normalizado primeiro. Em seguida, None é normalizado ao mesmo
    # marcador. Nenhum missing_value fica dependente do formato CSV/JSON.
    categorical = Pipeline([
        ("nan", SimpleImputer(strategy="constant", fill_value="__missing__", keep_empty_features=True)),
        ("null", SimpleImputer(missing_values=None, strategy="constant",
                               fill_value="__missing__", keep_empty_features=True)),
        ("codes", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                                 encoded_missing_value=-1)),
    ])
    return ColumnTransformer([
        ("amount", Pipeline([("float", numeric_cast()),
                              ("missing", SimpleImputer(strategy="constant", fill_value=0., keep_empty_features=True))]), ["amount"]),
        ("external", Pipeline([("float", numeric_cast()),
                                ("missing", SimpleImputer(strategy="constant", fill_value=-1., keep_empty_features=True))]), EXTERNAL),
        ("mcc", Pipeline([("float", numeric_cast()),
                           ("missing", SimpleImputer(strategy="constant", fill_value=-1., keep_empty_features=True)),
                           ("codes", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]), ["mcc"]),
        ("nominal", categorical, NAMES[5:]),
    ], verbose_feature_names_out=False)


def availability(frame: pd.DataFrame) -> np.ndarray:
    values = frame[EXTERNAL].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    return np.isfinite(values).sum(axis=1)


def make_encoder(original: np.ndarray) -> ColumnTransformer:
    # Os códigos ordinais servem somente como transporte. A rede vê one-hot;
    # não vê distâncias entre MCCs ou ordem artificial das categorias.
    nominal_indices = list(range(4, len(NAMES)))
    categories = [np.array(sorted(set(original[:, i]) | {-1.})) for i in nominal_indices]
    return ColumnTransformer([
        ("numeric", StandardScaler(), [0, 1, 2, 3]),
        ("nominal", OneHotEncoder(categories=categories, handle_unknown="ignore",
                                   sparse_output=False), nominal_indices),
        ("missing_scores", FunctionTransformer(functools.partial(np.equal, -1.)), [1, 2, 3]),
    ])


def augment_training(z: np.ndarray, y: np.ndarray, seed: int = 42) -> tuple[np.ndarray, np.ndarray, dict]:
    """Augmentação só no treino; nunca em calibração, validação ou background.

    Mantém todos os originais. Aplica as sete máscaras a todos os positivos;
    cada negativo recebe uma máscara adicional, atribuída sem usar seu score.
    Não adiciona casos em que nenhum score realmente observado restou.
    """
    rng = np.random.default_rng(seed)
    assignments = rng.integers(1, 8, size=len(y))
    xs, ys = [z], [y]
    counts = {}
    for mask in range(1, 8):
        selected = (y == 1) | ((y == 0) & (assignments == mask))
        copy = z[selected].copy()
        labels = y[selected].copy()
        for j in range(3):
            if not mask & (1 << j):
                copy[:, j + 1] = -1.
        valid = (copy[:, 1:4] >= 0).any(axis=1)
        xs.append(copy[valid]); ys.append(labels[valid])
        counts[str(mask)] = int(valid.sum())
    augmented, target = np.concatenate(xs), np.concatenate(ys)
    positive = np.flatnonzero(target == 1)
    n_neg = int((target == 0).sum())
    extra = max(0, int(n_neg * 0.10) - len(positive))
    if extra:
        indices = rng.choice(positive, size=extra, replace=True)
        augmented = np.concatenate([augmented, augmented[indices]])
        target = np.concatenate([target, target[indices]])
    order = rng.permutation(len(target))
    return augmented[order], target[order], {
        "original_rows": len(z), "mask_rows": counts, "training_rows": len(target),
        "training_positives": int(target.sum()), "all_missing_augmented": False,
    }


def fit_candidate(training: pd.DataFrame, seed: int = 42) -> tuple[ColumnTransformer, CalibratedClassifierCV, dict]:
    valid = training.loc[availability(training) > 0].copy()
    fit, calibration = train_test_split(valid, test_size=0.20, stratify=valid.feedback_label,
                                       random_state=seed)
    pre = make_preprocessor()
    z = pre.fit_transform(fit[NAMES])
    encoder = make_encoder(z)
    encoder.fit(z)  # original, sem duplicatas/augmentação/calibração
    augmented, labels, augmentation = augment_training(z, fit.feedback_label.to_numpy(int), seed)
    parameters = {**PARAMS, "random_state": seed}
    neural = MLPClassifier(**parameters)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        neural.fit(encoder.transform(augmented), labels)
    messages = [str(item.message) for item in caught]
    base = Pipeline([("representation", encoder), ("network", neural)])
    # cv=prefit nunca reajusta a rede. Dados de calibração são originais e
    # separados, sem oversampling e sem máscaras artificiais.
    calibrated = CalibratedClassifierCV(estimator=base, method="sigmoid", cv="prefit")
    calibrated.fit(pre.transform(calibration[NAMES]), calibration.feedback_label.to_numpy(int))
    calibrated.model_version_ = VERSION
    pre.model_version_ = VERSION
    info = {
        "fit_event_ids": fit.event_external_id.tolist(),
        "calibration_event_ids": calibration.event_external_id.tolist(),
        "fit_rows": len(fit), "fit_positives": int(fit.feedback_label.sum()),
        "calibration_rows": len(calibration),
        "calibration_positives": int(calibration.feedback_label.sum()),
        "natural_calibration_prevalence": float(calibration.feedback_label.mean()),
        "excluded_without_scores": len(training) - len(valid),
        "augmentation": augmentation, "iterations": int(neural.n_iter_),
        "convergence_warnings": messages,
        "model_input_slots": len(NAMES), "neural_input_features": int(neural.n_features_in_),
        "calibration_scope": "natural observed availability; outage probabilities require separate validation",
    }
    log.info("Rede v2: fit=%s calib=%s iterações=%s", len(fit), len(calibration), neural.n_iter_)
    return pre, calibrated, info


def scores(pre: ColumnTransformer, model: CalibratedClassifierCV, frame: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(pre.transform(frame[NAMES]))[:, 1]


def build_explainer(pre, model, fitting: pd.DataFrame, seed: int = 42, size: int = 256):
    original = fitting.loc[availability(fitting) > 0]
    pos, neg = original.loc[original.feedback_label.eq(1)], original.loc[original.feedback_label.eq(0)]
    if size < 2 or pos.empty or neg.empty:
        raise ValueError("Background exige pelo menos dois registros e as duas classes")
    npos = min(32, len(pos), max(1, size // 4))
    nneg = min(size - npos, len(neg))
    positive = pos.sample(n=npos, random_state=seed)
    negative = neg.sample(n=nneg, random_state=seed)
    reference = pd.concat([positive, negative])
    prevalence = len(pos) / len(original)
    weights = np.r_[np.full(npos, prevalence / npos), np.full(nneg, (1-prevalence) / nneg)]
    data = DenseData(pre.transform(reference[NAMES]), NAMES, None, weights)
    scalar = Pipeline([
        ("probability", FunctionTransformer(model.predict_proba)),
        ("positive", FunctionTransformer(operator.itemgetter((slice(None), 1)))),
    ])
    explanation = shap.KernelExplainer(scalar.transform, data, link="identity", feature_names=NAMES)
    explanation.model_version_ = VERSION
    explanation.background_event_ids_ = reference.event_external_id.tolist()
    explanation.background_class_weights_ = {"positive": prevalence, "negative": 1-prevalence}
    explanation.background_seed_ = seed
    return explanation
