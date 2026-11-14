# Modelo de fraude — v2.1.0

Rede neural MLP com camada oculta de 16 unidades, representação nominal interna,
indicadores de ausência, treino com indisponibilidade e calibração sigmoid.
Os nomes legados model_v1.pkl/pipeline_v1.pkl/explainer_v1.pkl são mantidos para
carregamento automático; a versão lógica é v2.1.0.

## Evidência e limites

- Dataset sintético: 132,275 linhas; período rotulado avaliado: jan–mar/2025.
- Seleção e cortes: somente predições out-of-fold dos dados de desenvolvimento da v1.
- AP média nos três folds: 1.000000; baseline máximo externo: 1.000000.
- Regressão histórica: 6,549 registros, 22 fraudes, AP 1.000000, ROC-AUC 1.000000.
- Corte de revisão: 0.472670; alta prioridade: 0.928562.
- Matriz no corte de revisão: [[6527, 0], [0, 22]]; Brier 0.000008435.
- Recall: 100.00%, IC binomial 95% [84.56%, 100.00%], sob independência.
- O teste histórico já foi inspecionado durante a auditoria v1: NÃO é validação cega nova.
- Validações temporais e separadas por cliente foram feitas só no desenvolvimento.
- 87 testes passaram sem falhas, erros ou skips. API real e recarga portátil verificadas.

Igualar a regra simples é uma condição mínima; não foi demonstrada superioridade
da rede ou generalização a fraude bancária real. A base sintética é facilmente
separável pelos scores externos. Dados rotulados adicionais são necessários.

## Dados e calibração

Rede ajustada em 20,951 registros originais, 70 fraudes.
Calibração independente em 5,238 registros naturais,
com 18 fraudes e sem duplicatas ou máscaras.
3 registros de desenvolvimento sem qualquer score foram excluídos do ajuste;
o serviço se abstém nessa condição. Seis fraudes de demonstração continuam fora
de ajuste, calibração, definição de cortes e background.

O ajuste neural inclui as sete combinações com ao menos um score observado.
Oversampling positivo/negativo 0,10 ocorre só no treino, depois do ajuste dos
transformadores em dados originais. A calibração tem poucos positivos e é
preliminar. Sob indisponibilidade artificial, não há certificação de calibração.

| Scores omitidos no estresse | AP dos casos atendidos | Fraudes detectadas/atendidas | FP | Fraudes encaminhadas por dados insuficientes |
|---|---:|---:|---:|---:|
| nenhum | 1.000000 | 22/22 | 0 | 0 |
| fraud_score | 1.000000 | 22/22 | 0 | 0 |
| visa_fraud_score | 1.000000 | 22/22 | 0 | 0 |
| mc_fraud_score | 1.000000 | 22/22 | 0 | 0 |
| visa_fraud_score, mc_fraud_score | 0.997835 | 21/21 | 1 | 1 |
| fraud_score, mc_fraud_score | 1.000000 | 20/20 | 0 | 2 |
| fraud_score, visa_fraud_score | 1.000000 | 20/20 | 1 | 2 |

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
256 registros originais de ajuste, incluindo 32 fraudes,
com pesos que reconstituem a prevalência natural. Referência não inclui calibração,
holdout ou demonstração. Valor-base 0.003364929; média no ajuste original
0.003364962. Erro máximo de aditividade: 1.11e-16.

Nas três referências alternativas pré-definidas, a menor similaridade média dos
três principais fatores dos exemplos positivos foi 1.0000. Maior diferença por
contribuição: 0.021006.
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
