# Entrega da issue #1 — scoring real e SHAP

Versão lógica: **v2.1.0**. Os nomes dos arquivos permanecem os exigidos na
issue; o sufixo `_v1` não identifica a versão lógica do modelo.

## Pacote de entrega

O arquivo local `models/model_artifacts_v2.zip` contém os cinco artefatos,
manifesto de integridade, evidências de validação e instruções.
O SHA-256 está em `models/model_artifacts_v2.zip.sha256`.

**Versione os três `.pkl` da versão ativa em `models/`**, junto ao código,
relatórios, contratos e manifesto. Por decisão posterior do responsável pelo
repositório, eles devem acompanhar o PR, substituindo a orientação original
da issue de não versioná-los. O ZIP continua ignorado; ele pode ser
compartilhado separadamente como alternativa de instalação.

## Instalar no serviço original

1. Use Python 3.12 e as dependências de `requirements-model.lock.txt`.
2. Copie os arquivos abaixo para `models/`, substituindo o conjunto anterior
   com a aplicação parada.
3. Reinicie a API para descartar os artefatos mantidos em cache.

| Arquivo | Conteúdo |
|---|---|
| `model_v1.pkl` | Rede neural MLP calibrada, serializada com joblib |
| `pipeline_v1.pkl` | Imputação e codificação das nove entradas |
| `explainer_v1.pkl` | Kernel SHAP da saída calibrada |
| `feature_names.json` | Ordem das nove features |
| `thresholds.json` | Limiares baixo/alto e versão `v2.1.0` |

O serviço original foi executado em processo novo, sem modificações e com
**somente esses cinco arquivos**: 11 casos geraram score real e cinco fatores
SHAP, com `mock_mode: false`. Não aplique um patch apenas para ativar esse modo.
Evidências: `models/legacy_scoring_validation.json` e
`models/validation_report.json`.

## Instalar nesta branch protegida

Esta branch já contém as proteções adicionais do scoring. Extraia também
`artifact_manifest.json`, obrigatório aqui, e reinicie a aplicação.
Não reaplique o patch de integração nesta branch. O ZIP fornece esse patch
somente para adicionar as proteções em outra cópia do serviço.

O dossiê encaminha `currency_code` ao `ScoreInput`. Em outra cópia do MVP,
confira esse encaminhamento: aceitar a moeda no schema não garante que o
construtor do dossiê a esteja enviando.

## Contratos e regras da issue

Ordem de entrada:

```json
["amount", "fraud_score", "visa_fraud_score", "mc_fraud_score", "mcc",
 "tipo_transacao", "modalidade_transacao", "pos_environment", "currency_code"]
```

`currency_code` participa do pré-processamento, do ajuste da rede e do SHAP.
Sua ausência é imputada pelo pipeline. Os valores preenchidos no case são
todos BRL; o desempenho para outras moedas não foi validado.

Imputação e codificação inicial ficam no pipeline externo; scaling, one-hot
nominal e indicadores de ausência ficam na representação interna do modelo.
Essa separação mantém uma contribuição SHAP por feature original.

| Requisito | Evidência |
|---|---|
| Todas as features obrigatórias da issue | `feature_names.json` e teste do pipeline ajustado |
| Score float em [0,1] | Validação de artefatos, API e serviço original |
| `fraud_score_model` separado de `fraud_score` | Schema e resposta do scoring |
| Top fatores com nome, direção, SHAP e valor | 11 casos reais de integração |
| `feedback_label`, `decision`, `transaction_status` fora das features | Lista de entrada e teste de regressão |
| Carregamento sem mudar o serviço original | Validação isolada com somente cinco arquivos |
| Entrega dos `.pkl` | Versão ativa liberada para commit, por decisão posterior à issue |

O contrato de saída mantém os campos pedidos: `event_external_id`,
`fraud_score_model`, `risk_level`, `top_factors`, `model_version` e `mock_mode`.
Na versão protegida, campos adicionais informam qualidade e disponibilidade.
Os scores externos usam a escala nativa do case, de 0 a 999; o score da rede
é uma saída distinta entre 0 e 1.

## Validação e limites

- 87 testes passaram, sem falhas, erros ou skips.
- Três folds estratificados, validação temporal e separação por cliente.
- AP média estratificada: 1,0; a regra de máximo dos scores externos também
  atingiu 1,0. Não foi demonstrada superioridade da rede.
- Regressão histórica: 22/22 fraudes detectadas, zero falsos positivos,
  no corte selecionado em desenvolvimento.
- Oito estados de disponibilidade, JSON inválido, corrupção, recarga,
  aditividade SHAP e concorrência verificados.

Dados sintéticos, teste histórico já inspecionado e calibração com poucos
positivos continuam limitações. Não há certificação para produção.
Consulte `models/model_card.md` e o protocolo v2.1 para os detalhes.
