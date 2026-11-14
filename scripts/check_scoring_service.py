"""Verifica os artefatos com o ScoringService existente, sem modificá-lo.

Extrai apenas o serviço e seus schemas de uma referência Git para um diretório
temporário e executa a inferência em outro processo, fora do código de treino.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


FILES = (
    "src/__init__.py",
    "src/api/__init__.py",
    "src/api/schemas/__init__.py",
    "src/api/schemas/score.py",
    "src/services/__init__.py",
    "src/services/scoring_service.py",
)

CHECK = r'''
import json, pathlib, sys, time
import numpy as np
from src.api.schemas.score import ScoreInput
from src.services.scoring_service import ScoringService

models = pathlib.Path(sys.argv[1])
cases = json.loads(pathlib.Path(sys.argv[2]).read_text())
names = json.loads((models / "feature_names.json").read_text())
service = ScoringService(models)
results = []
for case in cases:
    np.random.seed(42)
    started = time.perf_counter()
    result = service.predict(ScoreInput(**case))
    elapsed = time.perf_counter() - started
    assert not result.mock_mode, "O serviço caiu em modo mock"
    assert 0 <= result.fraud_score_model <= 1
    assert len(result.top_factors) == min(5, len(names)), "SHAP ausente"
    magnitudes = [abs(f.shap_value) for f in result.top_factors]
    assert magnitudes == sorted(magnitudes, reverse=True)
    for factor in result.top_factors:
        assert factor.feature in names, "Nome de feature incompatível"
        assert np.isfinite(factor.shap_value)
        expected = ("neutro" if abs(factor.shap_value) < 0.01 else
                    "aumenta_risco" if factor.shap_value > 0 else "reduz_risco")
        assert factor.direction == expected
    results.append({**result.model_dump(), "elapsed_seconds": elapsed})
print(json.dumps(results, ensure_ascii=False, allow_nan=False))
'''


def write_explanation_examples(report: dict, target: Path) -> None:
    results = report["results"]
    if not results:
        return
    version = results[0]["model_version"]
    lines = [f"# Exemplos de explicabilidade — modelo {version}", "",
             f"Resultados calculados para as entradas de validação; versão lógica {version}.",
             "Os exemplos posteriores a março demonstram inferência, sem avaliação de qualidade.", ""]
    examples = [("Maior score entre os exemplos", max(results, key=lambda r: r["fraud_score_model"])),
                ("Menor score entre os exemplos", min(results, key=lambda r: r["fraud_score_model"]))]
    for label, result in examples:
        lines += [f"## {label}: {result['event_external_id']}", "",
                  f"Score estimado: {result['fraud_score_model']:.8f}. Nível: {result['risk_level']}.",
                  "", "| Fator | Valor de entrada | Contribuição SHAP | Direção |",
                  "|---|---|---:|---|"]
        for factor in result["top_factors"]:
            lines.append(f"| {factor['feature']} | {factor['value']} | "
                         f"{factor['shap_value']:+.8f} | {factor['direction']} |")
        lines.append("")
    lines += ["A soma de todos os fatores e do valor-base reconstrói o score.",
              "As tabelas mostram somente os cinco maiores fatores; neutro significa |SHAP| < 0,01.",
              "As contribuições explicam associações do modelo, sem demonstrar causalidade ou fraude real.", ""]
    target.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--ref", default="origin/feat/st-1-infraestrutura-base")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    models, cases = args.models.resolve(), args.cases.resolve()
    commit = subprocess.check_output(
        ["git", "rev-parse", args.ref], text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="fio-scoring-contract-") as directory:
        root = Path(directory)
        for name in FILES:
            content = subprocess.check_output(["git", "show", f"{commit}:{name}"])
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        completed = subprocess.run(
            [sys.executable, "-c", CHECK, str(models), str(cases)],
            cwd=root, text=True, capture_output=True,
        )
        if completed.returncode:
            raise RuntimeError(f"Falha no serviço original:\n{completed.stderr}")
    report = {
        "service_commit": commit,
        "service_modified": False,
        "fresh_process": True,
        "results": json.loads(completed.stdout),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        write_explanation_examples(report, args.output.parent / "explanation_examples.md")
    print(rendered)


if __name__ == "__main__":
    main()
