"""Valida candidata real e gera evidências e model card, sem ativá-la."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from src.api.routers import score as score_router
from src.api.schemas.score import ScoreInput
from src.services.scoring_service import ScoringService


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--tests", type=Path, required=True)
    args = parser.parse_args()
    folder = args.models.resolve()
    metrics = json.loads((folder/"metrics.json").read_text())
    version = metrics["model_version"]
    if not metrics["candidate_passes_statistical_gates"] or not all(metrics["promotion_gates"].values()):
        raise RuntimeError("Candidata reprovada nos critérios estatísticos")
    junit = ET.parse(args.tests).getroot()
    suites = list(junit) if junit.tag == "testsuites" else [junit]
    tests = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
             for key in ["tests", "failures", "errors", "skipped"]}
    if tests["tests"] == 0 or tests["failures"] or tests["errors"] or tests["skipped"]:
        raise RuntimeError(f"Suíte incompleta: {tests}")
    subprocess.run([sys.executable, str(ROOT/"scripts/export_examples.py"), "--data", str(args.data),
                    "--models", str(folder)], check=True)
    cases = json.loads((folder/"example_transactions.json").read_text())
    # Adiciona explicitamente o falso negativo que motivou a auditoria.
    names = json.loads((folder/"feature_names.json").read_text())
    source = pd.read_csv(args.data, usecols=names+["event_external_id"], low_memory=False)
    old_fn = source.loc[source.event_external_id.eq("EVT_SYN_000002372")]
    if not old_fn.empty:
        record = json.loads(old_fn.to_json(orient="records"))[0]
        record["mcc"] = str(record["mcc"]) if record["mcc"] is not None else None
        cases.append(record)
    write_json(folder/"example_transactions.json", cases)
    # Prova o requisito da issue: somente os cinco arquivos, serviço original
    # intacto e processo novo, sem depender do manifesto ou do código atual.
    with tempfile.TemporaryDirectory(prefix="fio-issue1-five-files-") as temporary:
        five = Path(temporary)
        for name in ["model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl",
                     "feature_names.json", "thresholds.json"]:
            shutil.copy2(folder/name, five/name)
        subprocess.run([sys.executable, str(ROOT/"scripts/check_scoring_service.py"), "--models", str(five),
                        "--cases", str(folder/"example_transactions.json"), "--output", str(folder/"legacy_scoring_validation.json")],
                       check=True, stdout=subprocess.DEVNULL)
    service = ScoringService(folder)
    service.load_artifacts()
    app = FastAPI(); app.include_router(score_router.router)
    outcomes = []; scenarios = []
    with patch.object(score_router, "_get_scoring_service", lambda: service):
        with TestClient(app, raise_server_exceptions=False) as client:
            for record in cases:
                start = time.perf_counter(); response = client.post("/score", json=record)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["model_version"] == version and not result["mock_mode"]
                assert len(result["top_factors"]) == 5
                outcomes.append({**result, "elapsed_seconds": time.perf_counter()-start})
            complete = next(row for row in cases if all(row.get(k) is not None for k in names[1:4]))
            for mask in range(8):
                record = dict(complete)
                for i, field in enumerate(names[1:4]):
                    if not mask & (1 << i): record[field] = None
                response = client.post("/score", json=record)
                body = response.json()
                if mask == 0:
                    assert response.status_code == 422 and body["detail"]["code"] == "insufficient_data"
                    assert "fraud_score_model" not in body
                else:
                    assert response.status_code == 200, response.text
                    assert body["available_external_scores"] == mask.bit_count()
                    assert body["quality_status"] == ("complete" if mask == 7 else "degraded")
                    if mask != 7: assert body["quality_warnings"]
                scenarios.append({"availability_mask": mask, "http_status": response.status_code,
                                  "result": body})
            # JSON inválido deve ser rejeitado antes de chegar ao modelo.
            response = client.post("/score", content='{"event_external_id":"TECH","amount":NaN}',
                                   headers={"content-type":"application/json"})
            assert response.status_code == 422
    # Um único KernelExplainer compartilhado precisa preservar resultados entre requests.
    selected = cases[:2]
    expected = [service.predict(ScoreInput(**row)) for row in selected]
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(lambda row: service.predict(ScoreInput(**row)), selected))
    for a, b in zip(expected, concurrent):
        assert a.fraud_score_model == b.fraud_score_model
        assert [f.feature for f in a.top_factors] == [f.feature for f in b.top_factors]
        np.testing.assert_allclose([f.shap_value for f in a.top_factors],
                                   [f.shap_value for f in b.top_factors], atol=1e-8)
    with tempfile.TemporaryDirectory(prefix="fio-v2-corruption-") as temporary:
        tmp = Path(temporary)
        for name in ["model_v1.pkl", "pipeline_v1.pkl", "explainer_v1.pkl", "feature_names.json",
                     "thresholds.json", "artifact_manifest.json"]:
            shutil.copy2(folder/name, tmp/name)
        with (tmp/"model_v1.pkl").open("ab") as file: file.write(b"technical_corruption_test")
        try:
            ScoringService(tmp, allow_mock=True).load_artifacts()
            raise AssertionError("Corrupção foi aceita")
        except HTTPException as error:
            assert error.status_code == 503 and error.detail["code"] == "artifact_checksum_mismatch"
    report = {"model_version": version, "passed": True, "tests": tests,
              "legacy_five_file_load_compatible": True, "quality_guard_runtime_required": True,
              "legacy_validation_used_only_five_files": True,
              "required_input_features": names,
              "real_api_examples": outcomes, "availability_scenarios": scenarios,
              "nonfinite_json_rejected": True, "corrupt_artifacts_rejected": True,
              "shared_explainer_concurrency_validated": True,
              "statistical_gates": metrics["promotion_gates"]}
    report["runtime_source_sha256"] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        for name in ["src/services/scoring_service.py", "src/api/schemas/score.py",
                     "src/api/routers/score.py", "src/services/dossier_builder.py"]}
    legacy = json.loads((folder/"legacy_scoring_validation.json").read_text())
    report["legacy_service_commit"] = legacy["service_commit"]
    write_json(folder/"validation_report.json", report)
    write_json(folder/"scoring_validation.json", {"service_source": "src/services/scoring_service.py",
        "mock_mode": False, "results": outcomes})
    from check_scoring_service import write_explanation_examples
    write_explanation_examples({"results": outcomes}, folder/"explanation_examples.md")
    make_card(folder, metrics, report)
    manifest = json.loads((folder/"artifact_manifest.json").read_text())
    for name in ["model_card.md", "validation_report.json", "scoring_validation.json",
                 "explanation_examples.md", "example_transactions.json", "metrics.json"]:
        manifest["sha256"][name] = hashlib.sha256((folder/name).read_bytes()).hexdigest()
    manifest["release_validation_passed"] = True
    write_json(folder/"artifact_manifest.json", manifest)
    print(json.dumps({"passed": True, "tests": tests, "real_cases": len(outcomes),
                      "availability_states": len(scenarios)}, ensure_ascii=False))


def make_card(folder, m, validation):
    r=m["historical_regression"]; training=m["final_training"]; e=m["explainability"]
    rows=[]
    for mask in [7,6,5,3,1,2,4]:
        scenario=m["outages"][str(mask)]; s=scenario["metrics"]
        rows.append(f"| {', '.join(scenario['omitted']) or 'nenhum'} | {s['ap']:.6f} | "
                    f"{s['confusion_matrix'][1][1]}/{s['positives']} | {s['confusion_matrix'][0][1]} | "
                    f"{scenario.get('abstained_positives',0)} |")
    text=f'''# Modelo de fraude — {m['model_version']}

Rede neural MLP com camada oculta de 16 unidades, representação nominal interna,
indicadores de ausência, treino com indisponibilidade e calibração sigmoid.
Os nomes legados model_v1.pkl/pipeline_v1.pkl/explainer_v1.pkl são mantidos para
carregamento automático; a versão lógica é {m['model_version']}.

## Evidência e limites

- Dataset sintético: {m['dataset']['raw_rows']:,} linhas; período rotulado avaliado: jan–mar/2025.
- Seleção e cortes: somente predições out-of-fold dos dados de desenvolvimento da v1.
- AP média nos três folds: {m['development_mean_ap']:.6f}; baseline máximo externo: {m['development_mean_max_baseline_ap']:.6f}.
- Regressão histórica: {r['n']:,} registros, {r['positives']} fraudes, AP {r['ap']:.6f}, ROC-AUC {r['roc_auc']:.6f}.
- Corte de revisão: {m['thresholds']['baixo']:.6f}; alta prioridade: {m['thresholds']['alto']:.6f}.
- Matriz no corte de revisão: {r['confusion_matrix']}; Brier {r['brier']:.9f}.
- Recall: {r['recall']:.2%}, IC binomial 95% [{r['recall_ci95_binomial'][0]:.2%}, {r['recall_ci95_binomial'][1]:.2%}], sob independência.
- O teste histórico já foi inspecionado durante a auditoria v1: NÃO é validação cega nova.
- Validações temporais e separadas por cliente foram feitas só no desenvolvimento.
- {validation['tests']['tests']} testes passaram sem falhas, erros ou skips. API real e recarga portátil verificadas.

Igualar a regra simples é uma condição mínima; não foi demonstrada superioridade
da rede ou generalização a fraude bancária real. A base sintética é facilmente
separável pelos scores externos. Dados rotulados adicionais são necessários.

## Dados e calibração

Rede ajustada em {training['fit_rows']:,} registros originais, {training['fit_positives']} fraudes.
Calibração independente em {training['calibration_rows']:,} registros naturais,
com {training['calibration_positives']} fraudes e sem duplicatas ou máscaras.
{training['excluded_without_scores']} registros de desenvolvimento sem qualquer score foram excluídos do ajuste;
o serviço se abstém nessa condição. Seis fraudes de demonstração continuam fora
de ajuste, calibração, definição de cortes e background.

O ajuste neural inclui as sete combinações com ao menos um score observado.
Oversampling positivo/negativo 0,10 ocorre só no treino, depois do ajuste dos
transformadores em dados originais. A calibração tem poucos positivos e é
preliminar. Sob indisponibilidade artificial, não há certificação de calibração.

| Scores omitidos no estresse | AP dos casos atendidos | Fraudes detectadas/atendidas | FP | Fraudes encaminhadas por dados insuficientes |
|---|---:|---:|---:|---:|
{chr(10).join(rows)}

Os denominadores excluem abstenções; metrics.json também registra as contagens
totais e a captura incluindo abstenções como não detectadas automaticamente.
Com todos os três ausentes, a API retorna 422 insufficient_data, sem inventar
um score e sem simular resultado real. Com um ou dois, retorna degraded e aviso.

## Contrato e explicabilidade

Nove entradas: amount, fraud_score, visa_fraud_score, mc_fraud_score, mcc,
tipo_transacao, modalidade_transacao, pos_environment, currency_code. A moeda
participa do pré-processamento, do ajuste da rede e da explicabilidade.
No CSV, apenas BRL foi observado nos valores preenchidos; outras moedas não
têm desempenho validado. Valores ausentes são tratados pelo pipeline.
NaN e null são equivalentes;
MCC numérico ou string numérica é normalizado. O ordinal é apenas transporte:
a rede recebe codificação one-hot nominal interna, sem distâncias entre MCCs.

O Kernel SHAP explica nove slots originais da saída calibrada. Referência de
{e['background_size']} registros originais de ajuste, incluindo {e['background_positives']} fraudes,
com pesos que reconstituem a prevalência natural. Referência não inclui calibração,
holdout ou demonstração. Valor-base {e['base_value']:.9f}; média no ajuste original
{e['natural_fit_mean_score']:.9f}. Erro máximo de aditividade: {e['max_additivity_error']:.3g}.

Nas três referências alternativas pré-definidas, a menor similaridade média dos
três principais fatores dos exemplos positivos foi {min(s['mean_positive_top3_jaccard'] for s in e['stability']):.4f}. Maior diferença por
contribuição: {max(s['max_abs_contribution_difference'] for s in e['stability']):.6f}.
É um teste interno pequeno de estabilidade, sem interpretação causal.
O serviço serializa acesso ao explicador compartilhado para proteger seus buffers.

## Operação

Instale requirements-model.lock.txt. Os cinco artefatos são portáteis e não importam
classes do projeto. No serviço original, basta copiar os cinco arquivos para
models/ e reiniciar. A versão protegida desta branch exige adicionalmente
artifact_manifest.json, também incluído no ZIP; mantenha sua verificação ativa.
Para recusa por insuficiência, checksums, avisos de qualidade
e concorrência, aplique também a atualização do serviço em src/ (incluída no pacote).
Um backend antigo continua carregando os cinco arquivos, mas não implementa essas
proteções. Reinicie a aplicação após a troca; o serviço mantém carga em cache.

Os cortes foram selecionados no desenvolvimento para metas técnicas de recall
e limites de FPR documentados em thresholds.json. Não são custos ou capacidade
de análise aprovados pelo negócio. O risco médio pode não aparecer nesta base
facilmente separável; não foram fabricadas faixas para forçar sua ocorrência.

Sem garantia de produção: scores externos devem estar disponíveis na autorização
(D1); dados sintéticos, poucas fraudes, avaliação retrospectiva e dependência dos
scores de terceiros permanecem limitações. Decisão final sempre humana.

## Reprodução e integridade

Protocolo pré-definido: protocol.json; hashes do código e parâmetros: metrics.json.
IDs de ajuste, calibração, validação, demonstração e referência: split_manifest.json.
Checksums dos arquivos: artifact_manifest.json; evidências técnicas: validation_report.json.
Use scripts/train_model_v2.py e scripts/validate_release_v2.py antes de promover.
'''
    (folder/"model_card.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
