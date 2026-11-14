"""Publica somente candidata v2 aprovada, preservando a entrega anterior."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--models", type=Path, default=Path("models"))
    args = p.parse_args()
    candidate, target = args.candidate.resolve(), args.models.resolve()
    if candidate == target or target.parent != ROOT or args.models.is_symlink():
        raise ValueError("Use candidata separada e diretório de modelos diretamente no repositório")
    report = json.loads((candidate/"validation_report.json").read_text())
    metrics = json.loads((candidate/"metrics.json").read_text())
    if not report["passed"] or not all(metrics["promotion_gates"].values()):
        raise RuntimeError("Candidata sem aprovação completa")
    if report["tests"]["failures"] or report["tests"]["errors"] or report["tests"]["skipped"]:
        raise RuntimeError("Há falhas ou testes não executados")
    manifest = json.loads((candidate/"artifact_manifest.json").read_text())
    if not manifest.get("release_validation_passed") or not manifest.get("fresh_process_validation"):
        raise RuntimeError("Validação de release/recarga não registrada")
    for name, expected in manifest["sha256"].items():
        if Path(name).name != name:
            raise ValueError("Manifesto deve conter apenas arquivos do diretório do modelo")
        if hashlib.sha256((candidate/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Artefato modificado depois da validação: {name}")
    stage = Path(tempfile.mkdtemp(prefix=".models-staging-", dir=ROOT))
    previous = None
    try:
        shutil.copytree(candidate, stage, dirs_exist_ok=True)
        if target.exists():
            history = ROOT/"artifacts"; history.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            previous = history/f"before_v2_{stamp}"
            os.replace(target, previous)
        try:
            os.replace(stage, target)
        except BaseException:
            if previous is not None and not target.exists(): os.replace(previous, target)
            raise
    finally:
        if stage.exists(): shutil.rmtree(stage)
    print(f"v2 publicada em {target}; versão anterior preservada em {previous}")


if __name__ == "__main__":
    main()
