# Qualidade e recusa do scoring v2

O serviço em `src/services/scoring_service.py` usa os cinco nomes legados dos
artefatos, com versão lógica `v2.1.0`. A versão dos objetos, ordem das features,
limiares e checksums precisam concordar antes do uso.

## Comportamento da API

| Condição | Resultado |
|---|---|
| Três scores externos presentes | 200; `quality_status=complete` |
| Um ou dois scores presentes | 200; `quality_status=degraded`, contagem e avisos |
| Nenhum score externo presente | 422; `detail.code=insufficient_data`, sem score |
| Entrada incompatível, NaN/Infinity ou JSON inválido | 422 explícito |
| Artefato ausente, corrompido ou incompatível | 503 |
| Falha, valor não finito ou inconsistência no SHAP | 503 |
| Simulação solicitada explicitamente e arquivos ausentes | `mock_mode=true` |

`complete` refere-se à presença dos três scores externos, não à certificação
de todos os dados ou do modelo. Os scores externos estão na escala 0–1000;
o score da rede está em [0,1]. Ausência deve ser enviada como `null`, nunca
substituída artificialmente por zero, que é um score externo válido.

Na ausência dos três scores, o serviço não inventa uma probabilidade baixa,
média ou simulada. A transação precisa de informação adicional ou análise
humana. Uma abstenção não é contabilizada como transação legítima.

Exemplo de resposta com informação parcial:

```json
{
  "model_version": "v2.1.0",
  "mock_mode": false,
  "quality_status": "degraded",
  "available_external_scores": 2,
  "quality_warnings": ["Probabilidade sem calibração certificada sob indisponibilidade."]
}
```

Os campos anteriores continuam presentes em respostas 200: evento, score,
nível, cinco fatores SHAP e versão. Os novos campos têm defaults para
compatibilidade com consumidores existentes. Recomenda-se que a interface
mostre `quality_warnings`; a API já os fornece e o dossiê pode preservá-los
na serialização do resultado.

## Integridade e falhas

Para v2, `artifact_manifest.json` é obrigatório e deve listar hashes SHA-256
de todos os cinco arquivos requeridos. A verificação acontece antes do
joblib.load. Manifesto parcial ou corrupção não ativam simulação silenciosa.
Os três objetos serializados também precisam declarar a mesma versão lógica.

A ausência de arquivos só pode produzir simulação quando o construtor recebe
`allow_mock=True` explicitamente. O padrão é falhar com 503.
A compatibilidade de leitura de manifestos antigos limita-se a artefatos v1.

O serviço verifica formato e finitude da transformação, classes [0,1],
probabilidades em [0,1], forma SHAP (1,9) na v2.1, alinhamento com os nomes e aditividade
com tolerância absoluta 1e-6. Não há clamp silencioso de probabilidades inválidas.

O KernelExplainer reutiliza buffers internos: os cálculos são protegidos por
lock. A carga lazy também é protegida. Reinicie a aplicação após uma troca
de artefatos; não troque arquivos individualmente com o consumidor em execução.

## Implantação

Esta branch já inclui a aplicação completa e as proteções. Copie os artefatos
com seu manifesto e reinicie os servidores. O serviço aceita o contrato
anterior v2.0 de oito features e o v2.1 de nove, incluindo `currency_code`.
No dossiê, a moeda é encaminhada da transação para o `ScoreInput`.

Para adicionar as proteções em outra cópia da aplicação, o patch do pacote
tem base no commit registrado em `validation_report.json`; execute
`git apply --check` antes de aplicar. Confira também o encaminhamento de
`currency_code` ao scoring no construtor do dossiê.

Os pickles continuam portáteis e carregáveis pelo backend antigo. As checagens
descritas aqui pertencem ao serviço atualizado e não podem ser acrescentadas
ao backend antigo copiando apenas os cinco arquivos do modelo.

As proteções foram verificadas em testes técnicos e com os modelos reais,
incluindo oito estados de disponibilidade, corrupção, requisições simultâneas
e o falso negativo histórico da v1. Evidências: `models/validation_report.json`.
