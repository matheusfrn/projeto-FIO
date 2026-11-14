# Divergências e Decisões Pendentes — Projeto Fio

**Data da revisão:** 29/10/2026
**Autor:** Kaio
**Escopo da varredura:** todos os documentos do repositório (`docs/`, `notebooks/`, `data/`)

---

## Sumário Executivo

A varredura completa identificou **14 pontos de divergência** entre os documentos do projeto e os achados da EDA. Desses:

- **7 foram corrigidos diretamente** (inconsistências menores, sem impacto em decisão de produto ou arquitetura).
- **7 requerem sua decisão** — envolvem escolhas com trade-offs reais de escopo, qualidade de modelo, arquitetura ou prazo.

---

## Divergências Corrigidas Automaticamente

Estas foram resolvidas diretamente nos documentos. Nenhuma decisão sua é necessária.

| # | Documento(s) afetado(s) | O que foi | O que é agora |
|---|--------------------------|-----------|----------------|
| C1 | `arquitetura.md` Premissa 2 | "dados sintéticos" sem volume | Atualizado com 132.275 linhas, 116 fraudes, concentração jan–mar/2025 |
| C2 | `arquitetura.md` Seção 15.2 | "Desbalanceamento desconhecido" | Confirmado: 0,088% fraudes; estratégia PR-AUC + jan–mar/2025 como partição |
| C3 | `arquitetura.md` Seção 12.6 / RN-16 | Lista genérica de leakage | Detalhada com achados da EDA: `decision=deny`, `transaction_status=declined`, semântica ambígua de `fraud` |
| C4 | `fio-architecture-plan.md` ST-1 | "5 tabelas" | Corrigido para "7 tabelas" (correspondendo ao schema real) |
| C5 | `fio-architecture-plan.md` ST-2 | `fraud_score > 200` como proxy | Substituído por "percentil 95 de `fraud_score`" com nota sobre leakage de `decision=deny` |
| C6 | `arquitetura.md` C4 Context Diagram | "297 colunas" sem volume | Adicionado "132.275 linhas · 116 fraudes confirmadas" |
| C7 | `arquitetura.md` Histórico de Revisões | Versão 2.0 sem registro da EDA | Adicionada versão 2.1 com registro das atualizações pós-EDA |

---

## Divergências que Requerem Sua Decisão

---

### D1 — Scores externos (`fraud_score`, `visa_fraud_score`, `mc_fraud_score`) como feature ou leakage?

**Onde aparece:**
- `notebooks/eda-e-modelagem.ipynb` Etapas 3–4: esses três campos têm razão de separação ~4,4× entre fraudes e não-fraudes — os sinais mais fortes do dataset junto com velocidade e distância geográfica.
- `arquitetura.md` Seção 12.6: marcados como "pode ser leakage" (tratamento ambíguo).
- `planejamento.md` Seção 7.1 N1: o modelo deve ser "treinado com a base do case" sem restrição explícita a esses campos.

**A divergência:** a EDA mostra que esses campos são extremamente preditivos, mas não há confirmação se eles estariam disponíveis no momento real da decisão de fraude ou se são gerados após. Se forem scores de sistemas externos (bandeira do cartão) disponíveis no instante da autorização, são features legítimas. Se forem calculados depois, são leakage severo.

**Caminho A — Incluir como features:**
Ganho: modelo provavelmente com PR-AUC muito superior, caso com dados. Risco: se forem pós-evento, o modelo não funcionará em produção e os resultados na demonstração serão irreais.

**Caminho B — Excluir como precaução:**
Ganho: modelo mais conservador e defensável. Risco: perda significativa de poder preditivo nos dados sintéticos disponíveis; com 116 positivos e ausência de scores externos, o modelo pode ter desempenho mediano.

**Minha recomendação:** **Caminho B como padrão**, com teste paralelo de Caminho A documentado explicitamente como "condição com possível leakage". Apresentar os dois resultados separadamente na demonstração. Isso preserva a integridade da apresentação e ainda mostra o potencial do modelo com dados completos. Tim precisa confirmar a decisão.

---

### D2 — Particionamento temporal: os dados úteis para treino são apenas jan–mar/2025

**Onde aparece:**
- `notebooks/eda-e-modelagem.ipynb` Etapa 3: "todas as 116 fraudes estão concentradas em jan–mar/2025; de abril a dezembro a taxa de fraude é exatamente 0,0%".
- `arquitetura.md` ST-3 (plano): menciona "separação treino/teste evita vazamento temporal" sem detalhar o impacto.
- `planejamento.md` Seção 7.1 N1: "métricas em teste temporal" como critério de aceite.

**A divergência:** a arquitetura pressupõe que o modelo pode ser avaliado em partição temporal (meses mais recentes como teste). Mas se os meses posteriores não têm `feedback_label` confirmado, o "teste temporal" não é possível nos termos normais: não há rótulos para avaliar.

**Caminho A — Usar apenas jan–mar/2025 com validação cruzada estratificada:**
Treina e avalia exclusivamente no período com rótulos. Menor volume disponível para treino; impossibilita avaliação de drift temporal real.

**Caminho B — Usar meses sem rótulo como conjunto de inferência apenas (sem avaliação):**
Mostrar na demonstração que o modelo consegue produzir scores para transações de qualquer período — mas sem métricas de qualidade nesse conjunto. Avaliação fica restrita ao jan–mar com cross-validation.

**Caminho C — Replanejar a estratégia de modelagem com base nessa realidade:**
Aceitar que as métricas reportadas serão de validação cruzada no período jan–mar/2025 e informar isso claramente no model card e na apresentação. Não simular avaliação temporal onde não há rótulos.

**Minha recomendação:** **Caminho C**, que é o mais honesto e defensável. Acerta expectativas com o planejamento: o critério de aceite N1 deve ser ajustado para "validação cruzada estratificada sobre jan–mar/2025" em vez de "teste temporal". Tim precisa confirmar. O `planejamento.md` Seção 7.1 N1 precisará de atualização menor para refletir essa realidade.

---

### D3 — Comportamento agêntico (N6): a arquitetura atual não o suporta

**Onde aparece:**
- `docs/escopo-proposta-solucao.md` Seção 3.3.4: "A organização das consultas acompanhará os indícios encontrados. Quando a transação apresentar diferença relevante de valor, a investigação pode priorizar o histórico de compras. Quando o estabelecimento concentrar indícios, a consulta pode começar por seus eventos anteriores."
- `docs/planejamento.md` Seção 7.1 N6: "demonstrar duas rotas de consulta distintas e justificadas pelos indícios e um cenário de contexto insuficiente; preservar o rastro das etapas e fontes consultadas."
- `docs/arquitetura.md`: o `Dossier Builder` monta o dossiê em uma sequência fixa — não há lógica de roteamento condicional baseado em indícios. A máquina de estados não prevê ramificações de consulta adaptativas.

**A divergência:** o escopo descreve um comportamento agêntico condicional (a sequência de consultas muda conforme os indícios do caso). A arquitetura atual implementa um pipeline linear e fixo. O dossiê sempre busca score + desvios + timeline, na mesma ordem, independentemente do indício dominante.

**Caminho A — Implementar roteamento adaptativo real no Dossier Builder:**
Adicionar uma camada de "Intent Classifier" que analisa o score e os desvios e decide a ordem/prioridade das consultas (ex.: se `mcc_unusual=True`, priorizar histórico do estabelecimento antes do associado). Preservar rastro no dossiê. Mais trabalho para Thi; requer especificação mais detalhada.

**Caminho B — Simular comportamento agêntico na interface:**
O backend continua com pipeline fixo; o frontend apresenta os resultados de forma ordenada por "relevância do indício" (destaca o fator dominante, reordena as seções). Rastro de apresentação é registrado. Tecnicamente mais simples, narrativamente aceitável.

**Caminho C — Implementar como pipeline fixo com indicadores de prioridade:**
O Dossier Builder continua fixo mas adiciona ao dossiê um campo `investigation_priority` com o indício dominante identificado (ex.: `"valor_atipico"`, `"estabelecimento_historico"`, `"sem_historico_suficiente"`). A interface usa esse campo para destacar a seção relevante. É o meio-termo.

**Minha recomendação:** **Caminho C** para o MVP — satisfaz N6 com o mínimo de risco de prazo, preserva o rastro exigido pelo planejamento e é defensável como comportamento agêntico na apresentação. Se houver tempo, escalar para Caminho A. Igor e Thi precisam confirmar se Caminho C satisfaz a definição de N6 do planejamento.

---

### D4 — Comparação com cluster de perfil similar (C1): sem especificação técnica na arquitetura

**Onde aparece:**
- `docs/escopo-proposta-solucao.md` Seção 3.3: "A comparação com perfis semelhantes é funcionalidade complementar. Sua inclusão depende da disponibilidade de informações."
- `docs/canvas-proposta-de-valor.md` Seção 2.4.1: "Comparação com perfis semelhantes" como produto/serviço do Fio.
- `docs/planejamento.md` Seção 7.1 C1: condicionado à viabilidade dos dados — "registrar atributos, método de similaridade, janela, suporte mínimo e avaliação sem dados futuros."
- `docs/arquitetura.md`: a Visão Geral menciona "desvios em relação ao padrão individual e ao cluster", mas não há nenhum componente, tabela ou endpoint que implemente clustering.

**A divergência:** o canvas e a visão geral da arquitetura apresentam cluster como entrega. O escopo e o planejamento a condicionam corretamente à viabilidade dos dados. A EDA não analisou clustering de associados. A arquitetura não tem o componente.

**Caminho A — Implementar clustering básico (ex.: K-Means sobre features comportamentais):**
Adiciona `ClusteringService` ao backend, nova tabela `customer_clusters`, novo endpoint, nova seção no dossiê. Trabalho significativo; depende de Isa preparar features de agrupamento.

**Caminho B — Declarar C1 como "não entregue neste ciclo" e documentar no dossiê:**
O dossiê exibe um campo "Comparação com perfis similares: não disponível neste ciclo." A Visão Geral da arquitetura perde a referência a cluster. Canvas e diagnóstico mantêm como valor futuro. Narrativa honesta.

**Caminho C — Pseudo-cluster por segmento existente:**
Usar o campo `customer_segment` já disponível nos dados para agrupar associados "semelhantes" sem modelagem de clustering. Mostrar a taxa de fraude no segmento nas últimas N transações. Simples, entregável, mas limitado.

**Minha recomendação:** **Caminho B** como decisão segura para o prazo. A arquitetura e a Visão Geral devem remover a referência a "cluster" e substituir por "comparação com perfis similares (C1 condicionado à viabilidade dos dados — não entregue neste ciclo)." O canvas pode manter como valor futuro. Se houver tempo sobrando após N1–N6, Caminho C é implementável em um dia. Igor precisa confirmar a decisão de C1 conforme previsto no planejamento até 29/10.

---

### D5 — Registro salvável e recuperável da análise: não está implementado na arquitetura

**Onde aparece:**
- `docs/planejamento.md` Seção 7.1 N5: "permitir salvar e recuperar análise e justificativa, identificação do caso, evidências, momento de geração e versão do modelo."
- `docs/planejamento.md` Seção 9 conteúdo mínimo do dossiê: "Registro salvável e recuperável da análise e justificativa, caso, evidências, momento de geração e versão do modelo."
- `docs/arquitetura.md` RF-10: "O analista registra a decisão final (fraude / legítima / em análise)." — apenas o campo de decisão.

**A divergência:** o planejamento exige que o analista possa **salvar e recuperar** uma análise com texto livre de justificativa, vinculada ao caso. A arquitetura implementa apenas `analyst_decision` (enum) e `notes` (texto curto). Não há campo explícito para "justificativa longa", nem endpoint de recuperação por caso, nem garantia de que `model_version` e `generated_at` fiquem persistidos junto ao registro de decisão.

**O que está faltando na arquitetura:**
- Campo `justification_text` (texto livre, obrigatório?) na tabela `analyst_decisions` e no contrato do endpoint `POST /decision`.
- Garantia de que `model_version` e `generated_at` do dossiê ficam associados ao registro de decisão.
- Endpoint de recuperação `GET /alerts/{alert_id}/decision` ou via dossiê já persistido.

**Minha recomendação:** corrigir a arquitetura adicionando `justification_text` (TEXT, opcional para MVP) à tabela `analyst_decisions` e ao schema `DecisionRequest`. O dossiê já persiste `model_version` e `generated_at`. Endpoint de recuperação já existe via `GET /alerts/{alert_id}/dossier` que inclui `analyst_decision`. Essa correção é pequena e não exige decisão de produto — mas confirme se `justification_text` deve ser obrigatório ou opcional no MVP.

---

### D6 — Rastro das consultas adaptativas (N6): não está modelado no banco de dados

**Onde aparece:**
- `docs/planejamento.md` Seção 7.1 N6: "preservar o rastro das etapas e fontes consultadas."
- `docs/planejamento.md` Seção 9 conteúdo mínimo: "Rastro da ordem e do motivo das consultas e das lacunas encontradas."
- `docs/arquitetura.md`: sem campo de rastro no schema do dossiê, sem campo na tabela `dossiers`, sem menção ao rastro nos contratos.

**A divergência:** o planejamento é explícito que N6 exige rastro. A arquitetura não modela isso.

**O que está faltando:**
- Campo `investigation_trace` (JSON ou TEXT) na tabela `dossiers` e no contrato `DossierResponse`.
- Estrutura mínima do rastro: `[{step, source, reason, result, gaps_found}]`.

**Minha recomendação:** adicionar `investigation_trace` como campo JSON à tabela `dossiers` e ao `DossierResponse`. Para o MVP (Caminho C da D3), o trace é simples: registra qual indício foi identificado como dominante e quais seções foram priorizadas. Não requer decisão de produto — é implementação direta. Mas a decisão sobre D3 (nível de agentividade) condiciona o que vai nesse trace.

---

### D7 — Volume de dados para demonstração: 116 positivos limitam os "seis casos distintos" do protocolo de validação

**Onde aparece:**
- `docs/planejamento.md` Seção 7.2: "Igor consolida com Isa e Tim, até 30/10, pelo menos seis casos sintéticos distintos, organizados em três pares."
- `notebooks/eda-e-modelagem.ipynb` Etapa 3: 116 fraudes confirmadas, todas em jan–mar/2025.

**A divergência:** 116 positivos são suficientes para selecionar 6 casos de fraude. O risco está em dois pontos: (a) esses casos devem ser separados do conjunto de treino do modelo — com apenas 116 positivos, reservar 6 para demonstração reduz o treino; (b) o planejamento pede "pares de dificuldade semelhante" — com base sintética e padrões artificialmente claros (fraudes concentradas em ECOMMERCE + cartão não presente), todos os casos podem parecer "fáceis", tornando o pareamento de dificuldade difícil.

**Caminho A — Separar os 6 casos antes do treino:**
Garante isolamento correto. Custo: treina com 110 positivos em vez de 116 (impacto mínimo). Necessário para honrar o protocolo.

**Caminho B — Usar os 6 casos do conjunto de teste (sem vazar no treino):**
Equivalente ao A se o corte for feito antes. A diferença é semântica: A separa antes de tudo; B usa o conjunto de teste definido por Tim como fonte dos casos de demonstração.

**Minha recomendação:** **Caminho A** — Igor e Isa reservam 6 casos antes que Tim divida treino/teste. Isso preserva o protocolo e é trivial de implementar. O planejamento já prevê isso ("usar casos separados do treino"). Confirmar com Tim que ele saberá quais índices estão reservados antes de fazer o split.

---

## Tabela Consolidada de Decisões

| # | Decisão | Urgência | Responsável | Recomendação |
|---|---------|----------|-------------|--------------|
| D1 | Scores externos como feature ou leakage? | Alta (impacta modelagem) | Tim + Kaio | Excluir por padrão; teste paralelo com rótulo explícito |
| D2 | Estratégia de particionamento temporal | Alta (impacta N1) | Tim | Cross-validation estratificado jan–mar/2025; sem avaliação temporal em meses sem rótulo |
| D3 | Nível de agentividade do Dossier Builder | Alta (impacta N6 e arquitetura) | Kaio + Thi + Igor | Caminho C: pipeline fixo com campo `investigation_priority` |
| D4 | Cluster (C1): entregar ou declarar não entregue? | Alta (impacta prazo e narrativa) | Igor | Caminho B: declarar não entregue; remover da arquitetura; manter no canvas como futuro |
| D5 | Campo `justification_text` obrigatório ou opcional? | Média (impacta N5) | Igor | Opcional no MVP; adicionar ao schema da arquitetura |
| D6 | Estrutura do rastro de consultas (N6) | Média (depende de D3) | Kaio + Thi | Adicionar `investigation_trace` JSON ao dossiê; estrutura depende da decisão D3 |
| D7 | Separação dos 6 casos de demonstração antes do treino | Média (impacta protocolo §7.2) | Igor + Isa + Tim | Reservar antes do split; confirmar índices com Tim |

---

## Prazo das Decisões

Para não bloquear implementação:

- **D1, D2, D3, D4:** precisam ser decididas até **29/10** (hoje) — impactam a modelagem e a arquitetura.
- **D5, D6:** podem ser decididas até **30/10** — impactam o schema do banco e os contratos, mas Thi pode desenvolver com placeholder.
- **D7:** precisa ser resolvida até **30/10** para Igor ter os casos prontos conforme o planejamento.

---

## Registro de Resoluções — D1 a D7

Todas as divergências foram analisadas e decididas em **29/10/2026**. As decisões foram aplicadas nos documentos [`docs/arquitetura.md`](arquitetura.md), [`docs/fio-architecture-plan.md`](fio-architecture-plan.md), [`docs/planejamento.md`](planejamento.md), [`docs/escopo-proposta-solucao.md`](escopo-proposta-solucao.md) e [`docs/canvas-proposta-de-valor.md`](canvas-proposta-de-valor.md).

| # | Decisão | Caminho escolhido | Aplicado em | Responsável |
|---|---------|-------------------|-------------|-------------|
| D1 | Scores externos como feature ou leakage? | **Caminho A — Incluir como features**, com premissa explícita no model card de que a disponibilidade em produção deve ser validada pelo Sicredi. RN-17 adicionado à arquitetura. | `arquitetura.md` §12.6, §15.2, model card | Tim + Kaio |
| D2 | Particionamento temporal | **Caminho C — Validação cruzada estratificada sobre jan–mar/2025** como única partição com rótulos confirmados. Meses posteriores usados apenas para inferência. RN-18 adicionado. Critério N1 atualizado no planejamento. | `arquitetura.md` §15.2, `planejamento.md` §7.1 N1 | Tim |
| D3 | Nível de agentividade do Dossier Builder | **Caminho C — Pipeline fixo com campo `investigation_priority`** (5 valores possíveis) + `investigation_trace` JSON preservando o rastro. Interface destaca a seção prioritária. RN-19 adicionado. | `arquitetura.md` §5, §8, §12, schema dossiers | Kaio + Thi + Igor |
| D4 | Cluster (C1) | **Caminho B — Não entregue neste ciclo.** `cluster_comparison_status = "nao_entregue_neste_ciclo"` em todos os dossiês. Canvas mantém como valor futuro. Arquitetura e escopo removeram referências de entrega. | `arquitetura.md` visão geral, `escopo-proposta-solucao.md` §3.3, `planejamento.md` §7.1 C1 | Igor |
| D5 | Campo `justification_text` | **Opcional no MVP.** Campo `justification_text TEXT` adicionado à tabela `analyst_decisions` e ao schema `DecisionRequest`. | `arquitetura.md` §12.5, §13.3 | Igor |
| D6 | Rastro de consultas (`investigation_trace`) | **Adicionado.** Campo `investigation_trace TEXT` (JSON) na tabela `dossiers` e no contrato `DossierResponse`. Estrutura: `[{step, source, reason, result, gaps}]`. | `arquitetura.md` §12.4, §13.2 | Kaio + Thi |
| D7 | Separação dos 6 casos de demonstração | **Caminho A — Igor e Isa reservam 6 casos antes do split.** Tim recebe os índices reservados antes de dividir treino/validação. Protocolo documentado em §7.2 do planejamento. | `planejamento.md` §7.2 | Igor + Isa + Tim |

**Todas as 14 divergências estão encerradas.** Nenhuma decisão estrutural está pendente para início da implementação.

---
