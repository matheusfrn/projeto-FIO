# Treinamento e entrega do modelo v2

A versão ativa está em [models/model_card.md](../models/model_card.md).
O pacote local `models/model_artifacts_v2.zip`, entregue fora do Git, contém
os cinco artefatos, relatórios, versões de dependências e atualização do serviço
de scoring. Veja [a entrega da issue #1](entrega-issue-1.md).

Os arquivos mantêm os nomes `model_v1.pkl`, `pipeline_v1.pkl`,
`explainer_v1.pkl`, `feature_names.json` e `thresholds.json`.
O campo `model_version` identifica a versão lógica `v2.1.0`.

## Instalar na aplicação

Para executar esta branch, que já contém a aplicação completa e o serviço
atualizado, siga [execução local](execucao-local.md). O procedimento abaixo
se aplica à integração do pacote em outra cópia da aplicação.

Para o serviço original citado na issue #1, use Python 3.12 e dependências
compatíveis com `requirements-model.lock.txt`, copie somente os cinco arquivos
para `models/` e reinicie. O carregamento e os fatores SHAP são validados em
processo novo, com o código original intacto e apenas esses cinco arquivos.
Não é necessário aplicar um patch para ativar o scoring real nesse serviço.

Para instalar também as proteções adicionais desta branch:

1. Use Python 3.12 e as versões de `requirements-model.lock.txt`.
2. Extraia os cinco artefatos e o `artifact_manifest.json` para `models/`.
3. Aplique a atualização do serviço incluída em `integration/quality_guard.patch`.
   Na raiz da aplicação, rode primeiro `git apply --check CAMINHO/quality_guard.patch`
   e só então `git apply CAMINHO/quality_guard.patch`.
4. Reinicie a aplicação, que mantém os modelos carregados em cache.

O patch parte do commit da infraestrutura registrado em `validation_report.json`.
Se a aplicação já divergiu, revise o diff antes de aplicar.
Os arquivos de `src/` nesta branch já incluem a aplicação completa e as
alterações de scoring; não reaplique o patch nesta branch.

Os cinco pickles/JSON continuam carregando no serviço antigo. Contudo, a recusa
por insuficiência, verificação de integridade, avisos de qualidade e proteção de
concorrência exigem o serviço atualizado. Copiar somente os pickles não adiciona
essas proteções ao código antigo.

## Reproduzir a candidata

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-model.lock.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/train_model_v2.py \
  --data case_transacoes_cartoes_sintetico_900k.csv \
  --baseline-dir models --output models_candidate_v2_1_issue1
OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest -q tests --junitxml=/tmp/fio-v2-tests.xml
OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/validate_release_v2.py \
  --models models_candidate_v2_1_issue1 --data case_transacoes_cartoes_sintetico_900k.csv \
  --tests /tmp/fio-v2-tests.xml
.venv/bin/python scripts/promote_model_v2.py --candidate models_candidate_v2_1_issue1 --models models
.venv/bin/python scripts/package_artifacts.py --models models
```

O treinamento usa o manifesto versionado em `models/split_manifest.json` e
os casos em `models/demo_cases.json`. São os mesmos IDs reservados na v1;
não recrie um split mais favorável. A candidata preserva os manifestos e
registra o hash do manifesto de origem para rastreabilidade.

O CSV original não é modificado nem incluído no ZIP. A v1 e a pasta substituída
ficam preservadas em `artifacts/`, nos diretórios de backup ignorados pelo Git.
Por decisão posterior do responsável pelo repositório, os três `.pkl` da
versão ativa em `models/` devem ser commitados normalmente com `git add`,
junto aos JSONs e relatórios. A orientação original da issue foi substituída.
O ZIP continua ignorado e pode ser entregue por download ou anexo à issue/PR,
junto ao SHA-256 em `models/model_artifacts_v2.zip.sha256`.
CSV, banco local e ambientes virtuais também devem continuar fora do Git.

## Protocolo e avaliação

[protocolo-modelo-v2.1.json](protocolo-modelo-v2.1.json) registra antes do ajuste
a inclusão da moeda exigida na issue #1. Preserva as metas, arquitetura e
partições do [protocolo v2 original](protocolo-modelo-v2.json), sem alegar que
o holdout voltou a ser cego. Usa a mesma reserva de seis fraudes e os mesmos IDs de
desenvolvimento/holdout da v1. Somente o desenvolvimento é usado para ajuste,
calibração, escolha dos cortes e background SHAP.

A avaliação contém CV estratificada de três folds, validação temporal dentro
de janeiro–março e CV separada por cliente. Cada treino reserva 20% para
calibração sigmoid com registros originais e prevalência natural. O ajuste
neural vê augmentação das sete combinações com ao menos um score observado
e oversampling feito somente no treino.

O holdout antigo foi usado na auditoria e é identificado como regressão
histórica, não como avaliação cega nova. A v2 não tem certificação de produção.

As metas técnicas provisórias de recall/FPR estão em `thresholds.json`;
não representam custos ou capacidade de análise aprovados pelo Sicredi.
A base pode continuar sem casos de risco médio por ser facilmente separável.

## Representação e explicações

O pipeline externo normaliza nove entradas em nove slots, tratando `NaN`
e `null` igualmente. A codificação ordinal é apenas transporte: dentro do
estimador, MCC e as demais categorias viram one-hot nominal. A rede não
aprende distâncias numéricas entre códigos de atividades.

`currency_code` participa do pipeline, da rede e do SHAP; não foi apenas
acrescentado ao JSON. Os valores preenchidos no CSV são todos BRL, portanto
incluir a feature não comprova capacidade de generalizar para outras moedas.

O modelo contém MLP e calibrador sklearn padrões. O explicador é Kernel SHAP
da saída calibrada escalar. Sua referência tem 256 registros do ajuste, com
as duas classes e pesos da prevalência original. Aditividade, referências
alternativas, recarga e acesso concorrente foram verificados.
Os pickles não importam funções ou classes de `scripts/`.

Os [exemplos SHAP](../models/explanation_examples.md) apresentam fatores com
valor, contribuição e direção. Explicação associativa não demonstra causalidade
nem confirma fraude. O score permanece apoio à decisão humana.

## Limitações que código não elimina

Dados sintéticos, poucos positivos, teste já inspecionado e dependência de
scores externos continuam limitações. A calibração final usou apenas 18
fraudes separadas do ajuste; é preliminar. Sob indisponibilidade de scores,
o serviço sinaliza `degraded` e não declara calibração certificada.

A [auditoria v1](auditoria-modelo-v1.md) documenta os problemas que motivaram
a mudança. O model card e o validation report distinguem melhorias verificadas
de aspectos ainda dependentes de novos dados rotulados.
