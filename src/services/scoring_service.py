"""
scoring_service.py — Serviço de scoring e explicabilidade SHAP com checagem de qualidade v2.

Implementa a lógica de:
  1. Carregar artefatos do modelo (pipeline_v1.pkl, model_v1.pkl, explainer_v1.pkl,
     feature_names.json, thresholds.json) com verificação de checksums SHA256 contra
     artifact_manifest.json.
  2. Gerar score de risco [0,1] e top-5 fatores SHAP.
  3. Operar em modo mock APENAS quando allow_mock=True explícito no construtor (RN-11
     preservado para compatibilidade retroativa; novos consumidores usam default False).

Regras de negócio implementadas:
  RN-02  score é float [0,1]
  RN-03  limiares de risco via thresholds.json (padrão: baixo<0.40, alto>=0.70)
  RN-04  top-5 fatores por abs(shap_value); direção: aumenta/reduz/neutro
  RN-10  features ausentes preenchidas com mediana/moda do pipeline
  RN-11  mock score=0.5, risk_level="medio", mock_mode=True — APENAS se allow_mock=True
  RN-16  fraud_score/visa_fraud_score/mc_fraud_score são features de ENTRADA

Checagens de qualidade (guard v2):
  QG-01  Exige ao menos min_external_scores scores externos finitos em [0, 1000].
         Ausência de todos → HTTPException 422 (código insufficient_data).
         Nunca retorna score baixo nem mock por falta de dados.
  QG-02  Um ou dois scores externos → quality_status='degraded' + quality_warning
         sobre ausência de calibração certificada sob indisponibilidade.
  QG-03  Rejeita entradas não finitas ou fora de [0, 1000] nos scores externos.
         (Pydantic já bloqueia fora do range; guard captura NaN/±inf.)
  QG-04  Resultado do modelo não finito ou classes incorretas → HTTPException 503;
         sem clamp silencioso em valores inválidos.
  QG-05  Artefatos validados via SHA256 contra artifact_manifest.json (schema v2
         com seção "sha256"); corrupção → HTTPException 503.
  QG-06  Os cinco artefatos (model_v1.pkl, pipeline_v1.pkl, explainer_v1.pkl,
         feature_names.json, thresholds.json) são todos obrigatórios.
  QG-07  Se SHAP falhar, formato inválido ou aditividade > 1e-6 → HTTPException 503.
  QG-08  Modelo v2 esperado: CalibratedClassifierCV contendo Pipeline com
         OneHotEncoder+MLPClassifier; dimensão validada por versão:
         v2.0: oito colunas; v2.1: nove, incluindo currency_code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pathlib
import threading
from typing import Any

import numpy as np
from fastapi import HTTPException

from src.api.schemas.score import (
    EXTERNAL_SCORE_FIELDS,
    EXTERNAL_SCORE_MAX,
    ScoreInput,
    ScoreResult,
    TopFactor,
)

logger = logging.getLogger(__name__)

_MODELS_DIR = pathlib.Path("models")
_DEFAULT_THRESHOLDS = {"baixo": 0.40, "alto": 0.70}
_MOCK_VERSION = "mock-v0"
_SHAP_ADDITIVITY_TOL = 1e-6
_CANONICAL_FEATURES = ["amount", "fraud_score", "visa_fraud_score", "mc_fraud_score",
                       "mcc", "tipo_transacao", "modalidade_transacao", "pos_environment"]
_CURRENCY_FEATURES = _CANONICAL_FEATURES + ["currency_code"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_risk(score: float, thresholds: dict) -> str:
    """Classifica o score em baixo/medio/alto usando os limiares (RN-03)."""
    if score < thresholds["baixo"]:
        return "baixo"
    if score < thresholds["alto"]:
        return "medio"
    return "alto"


def _classify_direction(shap_value: float) -> str:
    """Classifica a direção de influência de um fator SHAP (RN-04)."""
    if abs(shap_value) < 0.01:
        return "neutro"
    return "aumenta_risco" if shap_value > 0 else "reduz_risco"


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _mock_top_factors(transaction: dict) -> list[TopFactor]:
    """Gera top_factors simulados para o modo mock (usado apenas quando allow_mock=True)."""
    candidates = [
        ("modalidade_transacao", transaction.get("modalidade_transacao", "desconhecido"), 0.30),
        ("pos_environment",      transaction.get("pos_environment", "desconhecido"),       0.25),
        ("amount",               transaction.get("amount", 0.0),                           0.20),
        ("fraud_score",          transaction.get("fraud_score"),                            0.18),
        ("card_presence_flag",   transaction.get("card_presence_flag", "desconhecido"),    0.12),
    ]
    factors = []
    for feature, value, shap_val in candidates:
        if value is None:
            continue
        factors.append(TopFactor(
            feature=feature,
            direction=_classify_direction(shap_val),
            shap_value=shap_val,
            value=value,
        ))
    return factors[:5]


# ---------------------------------------------------------------------------
# Contagem e validação de scores externos
# ---------------------------------------------------------------------------

def _count_finite_external_scores(transaction: dict) -> int:
    """Conta quantos scores externos são finitos e estão no range [0, EXTERNAL_SCORE_MAX]."""
    count = 0
    for field in EXTERNAL_SCORE_FIELDS:
        v = transaction.get(field)
        if v is not None and math.isfinite(float(v)) and 0.0 <= float(v) <= EXTERNAL_SCORE_MAX:
            count += 1
    return count


def _validate_input_external_scores(transaction: dict) -> None:
    """QG-03: rejeita scores externos não finitos (fora de [0, MAX] já é barrado pelo Pydantic)."""
    for field in EXTERNAL_SCORE_FIELDS:
        v = transaction.get(field)
        if v is not None and (not math.isfinite(float(v)) or not 0 <= float(v) <= EXTERNAL_SCORE_MAX):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_score_value",
                    "mensagem": f"Campo '{field}' contém valor não finito: {v}. "
                                "Scores externos devem ser finitos no intervalo [0, 1000].",
                    "campo": field,
                    "valor_recebido": repr(v),
                },
            )


# ---------------------------------------------------------------------------
# ScoringService
# ---------------------------------------------------------------------------

class ScoringService:
    """
    Serviço de scoring. Carrega artefatos do modelo uma única vez (lazy load)
    e reutiliza nas chamadas subsequentes.

    Parâmetros:
        models_dir   Diretório dos artefatos (default: 'models').
        allow_mock   Se True, ativa modo mock quando artefatos ausentes (RN-11).
                     Default False — novos consumidores devem lidar com 503 explícito.

    Uso (modo real, default):
        svc = ScoringService(models_dir=Path("artifacts/model_v1"))
        result = svc.predict(score_input)

    Uso (modo compatibilidade — mock habilitado):
        svc = ScoringService(allow_mock=True)
        result = svc.predict(score_input)   # retorna mock_mode=True se sem artefatos
    """

    def __init__(
        self,
        models_dir: pathlib.Path = _MODELS_DIR,
        allow_mock: bool = False,
    ) -> None:
        self._models_dir = pathlib.Path(models_dir)
        self._allow_mock = allow_mock
        self._pipeline = None
        self._model = None
        self._explainer = None
        self._feature_names: list[str] = []
        self._thresholds: dict = dict(_DEFAULT_THRESHOLDS)
        self._model_version: str = _MOCK_VERSION
        self._loaded: bool = False
        self._mock_mode: bool = False
        self._min_external_scores: int = 1  # default do protocolo v2
        self._load_lock = threading.RLock()
        self._shap_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Carregamento de artefatos
    # ------------------------------------------------------------------

    def load_artifacts(self) -> None:
        """Carrega todos os artefatos com validação de checksums (QG-05/QG-06).

        - Se allow_mock=True e artefatos ausentes: ativa modo mock (RN-11 retrocompatível).
        - Se allow_mock=False e artefatos ausentes ou corrompidos: lança HTTPException 503.
        - Explainer é sempre obrigatório (QG-06); explainer ausente → 503 em ambos os modos reais.
        """
        import joblib

        model_path      = self._models_dir / "model_v1.pkl"
        pipeline_path   = self._models_dir / "pipeline_v1.pkl"
        explainer_path  = self._models_dir / "explainer_v1.pkl"
        features_path   = self._models_dir / "feature_names.json"
        thresholds_path = self._models_dir / "thresholds.json"

        required_paths = [model_path, pipeline_path, explainer_path, features_path, thresholds_path]
        missing = [str(p) for p in required_paths if not p.exists()]

        if missing:
            if self._allow_mock:
                logger.warning(
                    "ScoringService: artefatos ausentes %s — ativando modo mock (RN-11, allow_mock=True).",
                    missing,
                )
                self._mock_mode = True
                self._loaded = True
                return
            # allow_mock=False → aborta com 503
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "artifacts_unavailable",
                    "mensagem": "Artefatos do modelo ausentes. Serviço indisponível.",
                    "artefatos_faltantes": missing,
                },
            )

        # --- QG-05: verificação de checksums SHA256 ---
        manifest_path = self._models_dir / "artifact_manifest.json"
        try:
            settings = json.loads(thresholds_path.read_text())
            schema_v2 = settings.get("artifact_schema_version", 1) >= 2 or str(settings.get("model_version", "")).startswith("v2.")
        except Exception as exc:
            raise HTTPException(503, detail={"code": "invalid_thresholds", "mensagem": "Arquivo de limiares inválido."}) from exc
        self._verify_checksums(
            manifest_path,
            {
                "model_v1.pkl": model_path,
                "pipeline_v1.pkl": pipeline_path,
                "explainer_v1.pkl": explainer_path,
                "feature_names.json": features_path,
                "thresholds.json": thresholds_path,
            },
            require_complete=schema_v2,
        )

        try:
            self._pipeline = joblib.load(pipeline_path)
            self._model = joblib.load(model_path)
            self._explainer = joblib.load(explainer_path)

            with open(features_path) as f:
                self._feature_names = json.load(f)

            with open(thresholds_path) as f:
                self._thresholds = json.load(f)

            self._model_version = self._thresholds.get("model_version", "v1.0.0")

            # --- leitura de política de input (thresholds.json pode ter seção input_policy) ---
            input_policy = self._thresholds.get("input_policy", {})
            self._min_external_scores = int(input_policy.get("min_external_scores", 1))
            if not 1 <= self._min_external_scores <= 3:
                raise ValueError("min_external_scores deve estar entre 1 e 3")
            expected_features = (_CURRENCY_FEATURES if self._model_version.startswith("v2.1.")
                                 else _CANONICAL_FEATURES)
            if self._feature_names != expected_features:
                raise ValueError("Contrato ou ordem das features incompatível")
            low, high = self._thresholds["baixo"], self._thresholds["alto"]
            if not (math.isfinite(low) and math.isfinite(high) and 0 < low < high <= 1):
                raise ValueError("Limiares inválidos")
            if not np.array_equal(self._model.classes_, [0, 1]):
                raise ValueError("Ordem de classes incompatível")
            if schema_v2:
                for artifact in [self._pipeline, self._model, self._explainer]:
                    if getattr(artifact, "model_version_", None) != self._model_version:
                        raise ValueError("Versões de pipeline, rede e explicador divergem")

            self._mock_mode = False
            self._loaded = True
            logger.info(
                "ScoringService: artefatos carregados — versão=%s min_external_scores=%d.",
                self._model_version,
                self._min_external_scores,
            )

        except HTTPException:
            raise
        except Exception as exc:
            msg = f"Erro ao carregar artefatos: {exc}"
            logger.error("ScoringService: %s", msg)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "artifacts_load_error",
                    "mensagem": msg,
                },
            )

    def _verify_checksums(
        self,
        manifest_path: pathlib.Path,
        artifacts: dict[str, pathlib.Path],
        require_complete: bool = False,
    ) -> None:
        """QG-05: verifica SHA256 dos artefatos obrigatórios contra artifact_manifest.json.

        Compatibilidade com manifest v1 (sem seção 'sha256'): se manifest ausente ou sem
        seção sha256, a checagem é omitida com aviso (preserva testes originais que não
        requerem manifest v2).
        """
        if not manifest_path.exists():
            if require_complete:
                raise HTTPException(503, detail={"code": "manifest_required", "mensagem": "Manifesto de integridade obrigatório para v2."})
            logger.warning(
                "ScoringService: artifact_manifest.json ausente em %s — "
                "verificação de checksums ignorada.",
                self._models_dir,
            )
            return

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "manifest_read_error",
                    "mensagem": f"Não foi possível ler artifact_manifest.json: {exc}",
                },
            )

        if not isinstance(manifest, dict) or not isinstance(manifest.get("sha256", {}), dict):
            raise HTTPException(503, detail={"code": "manifest_read_error", "mensagem": "Formato do manifesto inválido."})

        sha_map: dict[str, str] = manifest.get("sha256", {})
        if not sha_map:
            if require_complete:
                raise HTTPException(503, detail={"code": "manifest_incomplete", "mensagem": "Manifesto v2 sem hashes."})
            # manifest v1 sem checksums — compatibilidade com testes originais
            logger.info(
                "ScoringService: artifact_manifest.json sem seção 'sha256' — "
                "verificação de integridade ignorada (manifest v1)."
            )
            return

        errors: list[str] = []
        for name, path in artifacts.items():
            if name not in sha_map:
                if require_complete:
                    errors.append(f"{name}: hash obrigatório ausente")
                continue
            expected = sha_map[name]
            if not isinstance(expected, str) or len(expected) != 64:
                errors.append(f"{name}: hash inválido")
                continue
            actual = _sha256_file(path)
            if actual != expected:
                errors.append(
                    f"{name}: esperado={expected[:16]}… obtido={actual[:16]}…"
                )

        if errors:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "artifact_checksum_mismatch",
                    "mensagem": (
                        "Artefatos corrompidos ou substituídos. "
                        "Modelo real não pode ser utilizado."
                    ),
                    "erros": errors,
                },
            )

    def _ensure_loaded(self) -> None:
        with self._load_lock:
            if not self._loaded:
                self.load_artifacts()

    # ------------------------------------------------------------------
    # Predição
    # ------------------------------------------------------------------

    def predict(self, score_input: ScoreInput) -> ScoreResult:
        """Gera score + top-5 fatores SHAP para uma transação.

        Em modo real: valida entradas, verifica disponibilidade de scores externos
        e aplica os guards de qualidade v2.

        Em modo mock (somente allow_mock=True): retorna resultado simulado explicitamente
        sinalizado com mock_mode=True; guards de qualidade não se aplicam.
        """
        self._ensure_loaded()
        transaction = score_input.model_dump()

        if self._mock_mode:
            # Mock modo: explicitamente sinalizado — sem guards de qualidade
            return ScoreResult(
                event_external_id=score_input.event_external_id,
                fraud_score_model=0.5,
                risk_level="medio",
                top_factors=_mock_top_factors(transaction),
                model_version=_MOCK_VERSION,
                mock_mode=True,
                quality_status="degraded",
                available_external_scores=0,
                quality_warnings=["Modo mock ativo: artefatos do modelo indisponíveis."],
            )

        # --- Guards aplicados apenas em modo real ---

        # QG-03: rejeita não finitos
        _validate_input_external_scores(transaction)

        # QG-01: exige ao menos min_external_scores scores externos finitos
        available = _count_finite_external_scores(transaction)
        if available < self._min_external_scores:
            campos_faltantes = [
                f for f in EXTERNAL_SCORE_FIELDS
                if transaction.get(f) is None
                or not math.isfinite(float(transaction[f]))
            ]
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "insufficient_data",
                    "mensagem": (
                        f"É necessário ao menos {self._min_external_scores} score(s) externo(s) "
                        f"para gerar o score do modelo; recebidos {available}. "
                        "Dados insuficientes: encaminhe para análise."
                    ),
                    "campos_faltantes": campos_faltantes,
                },
            )

        return self._predict_real(score_input, transaction, available)

    def _predict_real(
        self,
        score_input: ScoreInput,
        transaction: dict[str, Any],
        available_external_scores: int,
    ) -> ScoreResult:
        """Executa predição com o modelo real."""
        import pandas as pd

        # Montar vetor de features na ordem esperada pelo pipeline (RN-10)
        row: dict[str, Any] = {f: transaction.get(f) for f in self._feature_names}
        X = pd.DataFrame([row])

        # Pipeline cuida de encoding, scaling e imputação (RN-10)
        try:
            X_transformed = self._pipeline.transform(X)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail={"code": "invalid_feature_values",
                "mensagem": "Valores de entrada incompatíveis com o pipeline."}) from exc

        # O contrato validado na carga determina a dimensão (v2.0: 8; v2.1: 9).
        n_features = len(self._feature_names)
        if X_transformed.shape != (1, n_features):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "pipeline_shape_error",
                    "mensagem": (
                        f"Pipeline retornou shape {X_transformed.shape}; "
                        f"esperado (1, {n_features}). Artefato incompatível."
                    ),
                },
            )

        # QG-04: valores do pipeline devem ser todos finitos
        if not np.all(np.isfinite(X_transformed)):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "pipeline_non_finite",
                    "mensagem": "Pipeline produziu valores NaN/inf. Artefato inválido.",
                },
            )

        try:
            proba = np.asarray(self._model.predict_proba(X_transformed), dtype=float)
        except Exception as exc:
            raise HTTPException(503, detail={"code": "model_prediction_error", "mensagem": "Falha na predição da rede."}) from exc
        if proba.shape != (1, 2):
            raise HTTPException(503, detail={"code": "model_shape_error", "mensagem": "Probabilidades com formato inválido."})

        # QG-04: classes do modelo devem ser [0, 1]
        if not np.array_equal(self._model.classes_, [0, 1]):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "model_classes_error",
                    "mensagem": (
                        f"Modelo retornou classes {list(self._model.classes_)}; "
                        "esperado [0, 1]. Artefato incompatível."
                    ),
                },
            )

        # QG-04: probabilidade deve ser finita
        fraud_score_raw = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
        if not math.isfinite(fraud_score_raw):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "model_non_finite_output",
                    "mensagem": f"Modelo retornou probabilidade não finita: {fraud_score_raw}.",
                },
            )

        # QG-04: sem clamp silencioso — resultado deve estar em [0,1]
        if not (0.0 <= fraud_score_raw <= 1.0):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "model_out_of_range",
                    "mensagem": (
                        f"Modelo retornou probabilidade fora de [0,1]: {fraud_score_raw}. "
                        "Artefato de calibração inválido."
                    ),
                },
            )

        risk_level = _classify_risk(fraud_score_raw, self._thresholds)

        # QG-07/QG-08: SHAP obrigatório
        top_factors = self._compute_shap(X_transformed, transaction)

        # QG-02: quality_status e warnings
        quality_status, quality_warnings = self._assess_quality(available_external_scores)

        return ScoreResult(
            event_external_id=score_input.event_external_id,
            fraud_score_model=fraud_score_raw,
            risk_level=risk_level,
            top_factors=top_factors,
            model_version=self._model_version,
            mock_mode=False,
            quality_status=quality_status,
            available_external_scores=available_external_scores,
            quality_warnings=quality_warnings,
        )

    # ------------------------------------------------------------------
    # SHAP — QG-07/QG-08
    # ------------------------------------------------------------------

    def _compute_shap(
        self,
        X_transformed: Any,
        transaction: dict[str, Any],
    ) -> list[TopFactor]:
        """QG-07/QG-08: SHAP obrigatório; falha ou formato inválido → HTTPException 503."""
        # QG-06: explainer obrigatório
        if self._explainer is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "explainer_unavailable",
                    "mensagem": "Explainer obrigatório ausente. Serviço indisponível.",
                },
            )

        try:
            # KernelExplainer reutiliza buffers internos; requests concorrentes
            # não podem compartilhar esses buffers enquanto ele está calculando.
            with self._shap_lock:
                raw = self._explainer.shap_values(X_transformed)
                shap_values = [np.array(v, copy=True) for v in raw] if isinstance(raw, list) else np.array(raw, copy=True)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "shap_computation_error",
                    "mensagem": f"Falha ao calcular SHAP: {exc}",
                },
            )

        # SHAP deve explicar exatamente as features do contrato carregado.
        # Normalizar para array 2D antes da checagem de formato
        if isinstance(shap_values, list):
            # KernelExplainer binário pode retornar lista de dois arrays; usar classe 1
            shap_values = np.array(shap_values[1]) if len(shap_values) == 2 else np.array(shap_values[0])
        else:
            shap_values = np.asarray(shap_values)

        # QG-07: formato deve ser (n, número de features do contrato).
        n_rows = X_transformed.shape[0]
        n_features = len(self._feature_names)
        if shap_values.ndim != 2 or shap_values.shape != (n_rows, n_features):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "shap_shape_error",
                    "mensagem": (
                        f"SHAP retornou shape {shap_values.shape}; "
                        f"esperado ({n_rows}, {n_features}). Explainer incompatível."
                    ),
                },
            )

        # QG-07: valores finitos
        if not np.all(np.isfinite(shap_values)):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "shap_non_finite",
                    "mensagem": "SHAP retornou valores NaN/inf.",
                },
            )

        # QG-07: aditividade — |sum(shap[i]) + base_value - proba[i]| < 1e-6
        try:
            base_value = float(self._explainer.expected_value)
            proba_row = float(self._model.predict_proba(X_transformed)[0][1])
            additivity_err = abs(float(shap_values[0].sum()) + base_value - proba_row)
            if not math.isfinite(base_value) or not math.isfinite(additivity_err) or additivity_err > _SHAP_ADDITIVITY_TOL:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "shap_additivity_error",
                        "mensagem": (
                            f"Erro de aditividade SHAP: {additivity_err:.2e} > "
                            f"{_SHAP_ADDITIVITY_TOL:.2e}. Explainer incompatível com o modelo."
                        ),
                        "additivity_error": additivity_err if math.isfinite(additivity_err) else "non_finite",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "shap_additivity_check_error",
                    "mensagem": f"Não foi possível verificar aditividade SHAP: {exc}",
                },
            )

        # Extrair linha 0 (única linha do request)
        vals = shap_values[0]

        if len(self._feature_names) != len(vals):
            raise HTTPException(503, detail={"code": "shap_feature_alignment_error", "mensagem": "Features e SHAP desalinhados."})
        feature_names = self._feature_names

        indexed = sorted(
            zip(feature_names, vals),
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:5]

        return [
            TopFactor(
                feature=feat,
                direction=_classify_direction(float(sv)),
                shap_value=float(sv),
                value=transaction.get(feat),
            )
            for feat, sv in indexed
        ]

    # ------------------------------------------------------------------
    # Avaliação de qualidade
    # ------------------------------------------------------------------

    def _assess_quality(
        self,
        available_external_scores: int,
    ) -> tuple[str, list[str]]:
        """QG-02: avalia qualidade da predição baseada na quantidade de scores externos.

        Com 3 scores: complete, sem avisos.
        Com 1 ou 2 scores: degraded + warning de ausência de calibração certificada.
        """
        total_external = len(EXTERNAL_SCORE_FIELDS)  # 3
        if available_external_scores >= total_external:
            return "complete", []

        warnings: list[str] = []
        missing_count = total_external - available_external_scores
        warnings.append(
            f"Score calculado sob indisponibilidade de {missing_count} score(s) externo(s) "
            f"(disponíveis: {available_external_scores}/{total_external}). "
            "A probabilidade estimada NÃO possui calibração certificada nesta condição."
        )
        return "degraded", warnings
