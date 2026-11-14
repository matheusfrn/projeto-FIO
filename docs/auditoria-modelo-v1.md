> Registro histórico da auditoria v1. A entrega ativa é v2; consulte models/model_card.md.
> A cópia local da entrega auditada está em artifacts/model_v1/.

# Auditoria crítica do modelo de fraude v1

Data: 04/11/2026. Auditoria executada sobre os cinco artefatos entregues.
Os artefatos oficiais e o código do repositório foram preservados. Os hashes
continuam iguais aos registrados em `models/artifact_manifest.json`.

## Veredito

A v1 funciona como entrega integrada do projeto, mas ainda não satisfaz um
critério exigente de robustez. Há uma dependência operacional frágil dos scores
externos, uma inconsistência confirmada de tratamento de nulos e desempenho
inferior a uma regra simples no próprio case. Eu bloquearia sua aprovação como
estimador confiável de risco para uso operacional até corrigir esses pontos.

Os 14 testes da entrega verificavam contratos, serialização e algumas proteções
de treinamento; não constituíam evidência suficiente de qualidade estatística
ou de resistência a indisponibilidade de atributos.

## 1. P1 — Dependência frágil de atributos opcionais

Simulei indisponibilidade de scores no holdout original, sem reajustar a rede.
Os cortes permaneceram fixos em 0,40. Este é um teste de estresse, não a taxa
normal de ausência observada no CSV.

| Cenário | Fraudes detectadas | Recall | AP |
|---|---:|---:|---:|
| Entradas originais | 21/22 | 95,45% | 0,975379 |
| `fraud_score` indisponível em todos os registros | 9/22 | 40,91% | 0,948560 |
| `visa_fraud_score` indisponível | 19/22 | 86,36% | 0,968549 |
| `mc_fraud_score` indisponível | 17/22 | 77,27% | 0,956086 |
| Todos os três scores indisponíveis | 0/22 | 0% | 0,002492 |

AP e recall em um corte fixo medem coisas diferentes: é possível continuar
ordenando razoavelmente os casos e deixar de sinalizá-los pelo corte operacional.

O falso negativo real do holdout é `EVT_SYN_000002372`:

- `fraud_score`: ausente;
- `visa_fraud_score`: 928;
- `mc_fraud_score`: 710;
- modalidade: cartão não presente, ambiente ECOMMERCE;
- score da rede: **0,0012846353**, classificado como baixo.

O explicador atribui aproximadamente -0,03106 ao `fraud_score` imputado:
a ausência substituída pela mediana contribui para reduzir o risco.
Os sinais de velocidade disponíveis no CSV, mas não recebidos pelo dossiê atual,
também eram fortes nesse caso: 22 transações no contador e velocidade 43.

Reajustei uma rede experimental sem os três scores, com o restante do protocolo
fixo: AP 0,105534 e recall@100 45,45%. Não é um estimador independente forte com
os outros cinco campos disponíveis. A premissa D1, de disponibilidade dos scores
externos antes da decisão, precisa ser confirmada com o provedor dos dados.

**Necessário:** treinar e avaliar explicitamente os cenários de ausência;
indicadores de ausência/estratégia apropriada de imputação; contrato de qualidade
da entrada e encaminhamento quando a informação for insuficiente. Não apresentar
um score baixo silenciosamente como evidência de legitimidade.

## 2. P1 — Nulo no CSV e nulo na API não são equivalentes no pipeline

O treinamento normaliza categorias ausentes para `np.nan`. O JSON/Pydantic
entrega `None`. O SimpleImputer categórico usa o padrão `missing_values=np.nan`;
o `None` segue para o OrdinalEncoder como categoria desconhecida, codificada -1.

Para `pos_environment`, a ausência no CSV vira aproximadamente **+2,7329** após
codificação e escala, enquanto `null` do JSON vira **-3,3315**.

Na reprodução da entrada normal do dossiê para o holdout, 74 scores mudaram mais
que 1e-8; mudança máxima 0,0001224, sem mudança de classe nessa amostra.
Essa comparação também reproduz o comportamento do importador para `amount`
ausente, que usa zero em vez da mediana do treinamento.

No teste específico em que o canal estava ausente em todos os registros, trocar
somente a representação `NaN` por `null` causou diferença de score de até
**0,682488**, com uma mudança de classe no corte 0,40. Foram 19 versus 20
fraudes detectadas, respectivamente. A direção da mudança não justifica o erro:
o mesmo significado de ausência deve produzir o mesmo resultado.

**Necessário:** normalização consistente antes da imputação e testes de igualdade
de predição para CSV/DataFrame/JSON, além de testes que apenas verifiquem finitude.

## 3. P1 — A rede perde para um baseline simples no próprio case

Além do score externo individual já documentado na entrega, comparei a rede
com a combinação dos três scores externos e modelos tradicionais.

| Método | AP no holdout | Fraudes detectadas no corte aplicável | Falsos positivos |
|---|---:|---:|---:|
| Rede v1, corte 0,40 | 0,975379 | 21/22 | 0 |
| Ranking pelo máximo dos três scores | 1,000000 | — | — |
| Ranking pela média dos três scores | 1,000000 | — | — |
| Regra de um corte no máximo dos scores | 1,000000 | 22/22 | 0 |
| Regressão logística balanceada, corte 0,40 | 1,000000 | 22/22 | 0 |
| Árvore de profundidade 3, corte 0,40 | 0,992424 | 22/22 | 2 |
| HistGradientBoosting, corte 0,40 | 0,989028 | 21/22 | 1 |

Os scores externos estão em escala 0–999: os rankings não foram avaliados com
um corte de probabilidade 0,40. A regra de um corte foi ajustada somente no
desenvolvimento: **máximo dos três scores > 697**. A mediana usada para preencher
scores ausentes foi calculada somente no desenvolvimento.

No desenvolvimento, o maior máximo dos scores entre legítimas era 637; o menor
entre fraudes era 757. Esse intervalo revela uma separação particularmente fácil
no gerador sintético. Isso não demonstra que a regra funcionará em dados reais.

Para evitar apoiar o julgamento apenas no holdout já inspecionado, repeti a
comparação nos mesmos três folds de desenvolvimento, com imputação ajustada
dentro de cada fold:

| Fold | AP rede v1 | AP máximo externo | Regra: fraudes perdidas / falsos positivos |
|---|---:|---:|---|
| 1 | 0,979602 | 1,000000 | 0 / 0 |
| 2 | 0,993694 | 1,000000 | 0 / 0 |
| 3 | 1,000000 | 1,000000 | 0 / 0 |

A comparação original apenas com um score individual subestimou a força de
um baseline combinado. Não está demonstrado valor adicional da complexidade
da rede para este conjunto de dados.

**Necessário:** benchmark pré-definido que inclua combinações de scores e modelos
simples; se a rede é requisito acadêmico, exigir ao menos paridade com esse
baseline e vantagem verificável em robustez. Mais camadas não resolvem a falta
de casos difíceis ou de evidência de generalização.

## 4. P2 — Evidência estatística pequena e score sem calibração comprovada

O holdout tem 6.549 registros, mas só **22 fraudes**, com 21 acertos.
Intervalos binomiais exatos de 95%, supondo observações independentes:

- recall de 95,45%: intervalo **77,16%–99,88%**;
- precisão de 100% em 21 previsões positivas: **83,89%–100%**;
- zero falsos positivos entre 6.527 negativos: limite superior bilateral de
  aproximadamente **0,0565%** para a taxa de falsos positivos.

As dependências entre entidades podem limitar a interpretação desses intervalos.
Não se deve vender os pontos estimados como garantias de produção.

Bootstrap pareado estratificado com 1.500 reamostragens do holdout:

- AP da rede: intervalo percentil **0,9192–1,0000**;
- diferença de AP rede menos `fraud_score` individual: aproximadamente
  **0–0,0704**; o limite inferior não demonstra vantagem estritamente positiva;
- ganho de recall@100: **0–0,1364**.

São intervalos condicionais aos mesmos 22 casos, não evidência de validade futura.

O oversampling muda a prevalência de cerca de 0,336% no desenvolvimento para
aproximadamente 9,09% nos dados apresentados ao otimizador. Não houve ajuste
posterior de calibração em amostra independente. O Brier baixo é favorável,
mas não certifica calibração: mede efeitos de calibração e discriminação.
Referência: [scikit-learn — calibração](https://scikit-learn.org/1.5/modules/calibration.html).

Não apareceu nenhum caso de risco médio no holdout: os cortes 0,40 e 0,70
geraram exatamente as mesmas decisões. Os cortes são contratuais e não têm
justificativa demonstrada de custo, capacidade de análise ou probabilidade real.

**Necessário:** novos exemplos rotulados, avaliação de calibração independente
com prevalência natural e critérios operacionais fixados antes da avaliação.

## 5. P2 — Explicação matematicamente válida, referência pouco representativa

O explicador usa 32 registros de desenvolvimento e **zero fraudes** no background.
Seu valor-base é 0,000002129, enquanto a média das predições no desenvolvimento
é 0,003395. Portanto, não representa o score médio da população de desenvolvimento.
Isso não torna o SHAP aritmeticamente incorreto; limita a referência interpretativa.

Em seis transações e 15 reamostragens alternativas de 32 registros:

- uma contribuição por feature mudou até **0,08334** em relação à referência oficial;
- a similaridade média dos conjuntos de três principais fatores variou de 0,60 a 1;
- uma referência com uma fraude mudou o valor-base para aproximadamente 0,03128;
- o score da rede permaneceu o mesmo; variaram as atribuições.

A aditividade do explicador oficial foi confirmada, com erro máximo de cerca de
1,1e-16. Mas aditividade não demonstra estabilidade ou causalidade.
A [documentação do KernelExplainer](https://shap.readthedocs.io/en/stable/generated/shap.KernelExplainer.html)
explica a função do background na substituição das features.

**Necessário:** referência maior/representativa, ponderação quando aplicável,
teste de estabilidade e análise da correlação entre os scores externos.

## 6. O que resistiu à auditoria

- Reprodução das métricas originais e integridade dos artefatos.
- Pré-processamento ajustado no treino de cada fold; oversampling só depois
  desse ajuste e só no treino. Não identifiquei entrada direta de target ou IDs.
- Validação temporal adicional apenas no desenvolvimento: janeiro→fevereiro,
  AP 1,0000; janeiro–fevereiro→março, AP 0,9834.
- CV adicional separada por cliente no desenvolvimento: AP 1,0000; 0,9908; 1,0000.

Esses testes favoráveis reduzem a suspeita de que o resultado dependa somente
do split aleatório. Ainda usam o mesmo gerador sintético e poucas fraudes.
Não comprovam disponibilidade dos scores externos nem generalização bancária real.

O holdout original compartilha clientes com desenvolvimento em 4.453/6.549
registros e estabelecimentos em 6.118/6.549; os estabelecimentos das 22 fraudes
já aparecem no desenvolvimento. Como IDs não são features, isso isoladamente
não prova vazamento. A [documentação de validação cruzada](https://scikit-learn.org/1.5/modules/cross_validation.html)
fundamenta a distinção entre partições aleatórias, por grupo e temporais.

MCC tratado como número contínuo e codificação ordinal de categorias são
compromissos de integração; devem ser comparados a representações semanticamente
apropriadas. Preservar o contrato do serviço não é evidência de qualidade dessas escolhas.

## Próxima versão e critérios de aprovação

1. Corrigir paridade de nulos e definir uma política explícita para indisponibilidade
   dos scores. Qualquer necessidade de flag de qualidade/abstenção deve ser tratada
   no contrato do serviço; não substituída por uma probabilidade arbitrária.
2. Redefinir o benchmark antes do próximo treino, incluindo a regra do máximo,
   regressão logística e cenários de ausência. Manter a rede como requisito de
   projeto não dispensa demonstrar utilidade comparativa.
3. Acrescentar atributos comportamentais disponíveis na autorização, validando
   rigorosamente seu momento de geração. Isso pode exigir evolução do contrato
   da aplicação e da agregação de SHAP por feature original.
4. Reservar uma avaliação nova, temporal e/ou por entidade, que não seja reutilizada
   para decidir os próximos ajustes. Os resultados explorados nesta auditoria
   não devem ser apresentados como teste final cego da futura v2.
5. Calibrar e escolher cortes conforme custos/capacidade de investigação e
   prevalência esperada, documentando intervalos e limitações.
6. Validar estabilidade de SHAP e condições de drift/ausência antes de aprovar.

A solidez pretendida exige, além de engenharia, dados rotulados mais difíceis e
representativos. Alterar hiperparâmetros até obter 100% neste case não produziria
essa evidência.

## Evidências e reprodução

- Resultados: `/tmp/fio-audit-results.json`.
- Auditoria principal: `/tmp/fio_model_audit.py`.
- Baselines comparáveis: `/tmp/fio_audit_followup.py`.
- Métricas oficiais: `models/metrics.json`.
- Todas as experiências novas foram diagnósticas; nenhum modelo alternativo
  foi promovido, publicado ou usado para sobrescrever a entrega v1.
