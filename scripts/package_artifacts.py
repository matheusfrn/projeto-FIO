"""Empacota a entrega validada, sem dados brutos ou código de treino no pickle."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


REQUIRED = (
    "model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
    "feature_names.json", "thresholds.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()
    manifest = json.loads((args.models / "artifact_manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        actual = hashlib.sha256((args.models / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Checksum incompatível: {name}")
    if not all((args.models / name).is_file() for name in REQUIRED):
        raise ValueError("A entrega exige os cinco artefatos")
    project = Path(__file__).resolve().parents[1]
    settings = json.loads((args.models/"thresholds.json").read_text())
    major = settings.get("model_version", "v1.0.0").split(".")[0]
    is_v2 = major == "v2"
    integration_patch = ""
    runtime_sources = {}
    if is_v2:
        validation = json.loads((args.models/"validation_report.json").read_text())
        if not validation["passed"] or not manifest.get("release_validation_passed"):
            raise ValueError("Candidata v2 sem validação de release")
        runtime_sources = validation["runtime_source_sha256"]
        for name, expected in runtime_sources.items():
            new = (project/name).read_text()
            if hashlib.sha256((project/name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Serviço alterado após a validação: {name}")
            old = subprocess.check_output(["git", "show", f"{validation['legacy_service_commit']}:{name}"], text=True)
            integration_patch += "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                fromfile=f"a/{name}", tofile=f"b/{name}"))
    output = args.models / f"model_artifacts_{major}.zip"
    temporary = output.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in REQUIRED + ("model_card.md", "metrics.json", "artifact_manifest.json",
                                "scoring_validation.json", "example_transactions.json",
                                "explanation_examples.md"):
            bundle.write(args.models / name, arcname=name)
        for name in ("requirements-model.txt", "requirements-model.lock.txt"):
            bundle.write(project / name, arcname=name)
        if is_v2:
            for name in ("validation_report.json", "protocol.json", "legacy_scoring_validation.json",
                         "split_manifest.json", "demo_cases.json"):
                bundle.write(args.models/name, arcname=name)
            for name in runtime_sources:
                bundle.write(project/name, arcname="integration/"+name)
            bundle.writestr("integration/quality_guard.patch", integration_patch)
            bundle.write(project / "docs/entrega-issue-1.md", arcname="ENTREGA-ISSUE-1.md")
        bundle.writestr("LEIA-ME.txt", (
            f"Modelo {settings['model_version']}; entrega da issue #1.\n"
            "Extraia estes arquivos no diretório models/ da aplicação.\n"
            "Use Python 3.12 e as dependências de requirements-model.lock.txt.\n"
            "Reinicie a aplicação após a cópia; o serviço faz carga em cache.\n"
            "Os cinco arquivos .pkl/.json requeridos não dependem de código customizado.\n"
            "Métricas e limitações: model_card.md e metrics.json.\n"
            "Versione os três .pkl ativos em models/ junto aos JSONs e ao manifesto.\n"
            "O ZIP continua fora do Git e pode ser compartilhado separadamente.\n"
            + ("No serviço original da issue #1, copiar os cinco arquivos e reiniciar\n"
               "ativa o scoring real sem mudança de código; não aplique patch só para isso.\n"
               "Na versão protegida desta branch, inclua também artifact_manifest.json.\n"
               "Não reaplique o patch nesta branch. Para instalar as proteções em outra cópia:\n"
               "v2: os pickles mantêm nomes legados. O backend antigo consegue carregá-los,\n"
               "mas os controles de insuficiência, checksums e concorrência exigem o serviço atualizado.\n"
               "Na raiz da aplicação, verifique e aplique integration/quality_guard.patch com\n"
               "git apply --check CAMINHO/quality_guard.patch e git apply CAMINHO/quality_guard.patch.\n"
               "O patch parte do commit registrado em validation_report.json. Faça revisão se sua aplicação divergir.\n"
               "Dados sintéticos; teste retrospectivo; calibração preliminar. Sem certificação de produção.\n" if is_v2 else "")
        ))
    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n")
    print(f"Pacote: {output} ({output.stat().st_size:,} bytes)\nSHA-256: {digest}")


if __name__ == "__main__":
    main()
