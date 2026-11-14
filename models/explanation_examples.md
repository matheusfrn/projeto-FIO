# Exemplos de explicabilidade — modelo v2.1.0

Resultados calculados para as entradas de validação; versão lógica v2.1.0.
Os exemplos posteriores a março demonstram inferência, sem avaliação de qualidade.

## Maior score entre os exemplos: EVT_SYN_000049973

Score estimado: 0.95000002. Nível: alto.

| Fator | Valor de entrada | Contribuição SHAP | Direção |
|---|---|---:|---|
| fraud_score | 999.0 | +0.34727015 | aumenta_risco |
| visa_fraud_score | 890.0 | +0.33361047 | aumenta_risco |
| mc_fraud_score | 826.0 | +0.22102323 | aumenta_risco |
| tipo_transacao | credito | +0.01849564 | aumenta_risco |
| modalidade_transacao | cartao_nao_presente | +0.01175615 | aumenta_risco |

## Menor score entre os exemplos: EVT_SYN_000000004

Score estimado: 0.00019149. Nível: baixo.

| Fator | Valor de entrada | Contribuição SHAP | Direção |
|---|---|---:|---|
| fraud_score | 159.0 | -0.00127929 | neutro |
| visa_fraud_score | 155.0 | -0.00093246 | neutro |
| mc_fraud_score | 187.0 | -0.00080040 | neutro |
| amount | 11.24 | -0.00011253 | neutro |
| tipo_transacao | debito | -0.00004137 | neutro |

A soma de todos os fatores e do valor-base reconstrói o score.
As tabelas mostram somente os cinco maiores fatores; neutro significa |SHAP| < 0,01.
As contribuições explicam associações do modelo, sem demonstrar causalidade ou fraude real.
