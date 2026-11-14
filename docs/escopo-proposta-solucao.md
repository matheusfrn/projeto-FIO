<a id="3-definicao-do-escopo-e-da-proposta-da-solucao"></a>
# 3. Definição do Escopo e da Proposta da Solução

## Sumário

- [3.1 Introdução](#31-introducao)
- [3.2 O escopo no contexto do Fio](#32-o-escopo-no-contexto-do-fio)
- [3.3 O que está dentro do escopo](#33-o-que-esta-dentro-do-escopo)
  - [3.3.1 Avaliação de risco da transação](#331-avaliacao-de-risco-da-transacao)
  - [3.3.2 Explicação dos fatores de risco](#332-explicacao-dos-fatores-de-risco)
  - [3.3.3 Plataforma de investigação](#333-plataforma-de-investigacao)
  - [3.3.4 Investigação orientada pelos indícios do caso](#334-investigacao-orientada-pelos-indicios-do-caso)
- [3.4 O que está fora do escopo](#34-o-que-esta-fora-do-escopo)
- [3.5 O papel do IBM Bob no desenvolvimento](#35-o-papel-do-ibm-bob-no-desenvolvimento)
- [3.6 Conclusão](#36-conclusao)
- [Referências](#referencias)

---

<a id="31-introducao"></a>
## 3.1 Introdução

&emsp;Este documento apresenta as entregas previstas para a versão de demonstração do Fio e seus limites de atuação. O escopo concentra a solução no apoio à investigação de transações suspeitas, por meio de um dossiê que reúna contexto, avaliação de risco e registro da decisão humana.

&emsp;A apresentação clara das informações e a possibilidade de questionar a avaliação seguem princípios de interação humano–IA voltados à compreensão e ao controle do usuário (AMERSHI *et al.*, 2019).

---

<a id="32-o-escopo-no-contexto-do-fio"></a>
## 3.2 O escopo no contexto do Fio

&emsp;O ponto de partida do Fio é uma transação sinalizada como suspeita. A solução apoiará o analista na consulta ao histórico, na interpretação dos indícios e na organização de uma justificativa para a decisão sobre o caso.

&emsp;O produto principal será o dossiê investigativo. Nele, o profissional encontrará o contexto da ocorrência e poderá relacionar a avaliação do modelo às informações disponíveis. A proposta combina a organização automática das informações com o julgamento do analista, preservando o controle humano sobre a decisão (SHNEIDERMAN, 2020).

---

<a id="33-o-que-esta-dentro-do-escopo"></a>
## 3.3 O que está dentro do escopo

&emsp;A primeira versão contempla quatro frentes: avaliação do risco da transação, explicação dos fatores relevantes, plataforma de investigação e organização das consultas conforme os indícios do caso. Essas frentes se traduzem nas seguintes entregas para o analista:

| Capacidade | Entrega prevista |
|---|---|
| Avaliar a indicação de risco | Indicador de risco da transação acompanhado dos fatores que contribuíram para seu resultado |
| Compreender o contexto do caso | Histórico do associado, mudanças em seu padrão de compras e eventos anteriores relacionados ao estabelecimento |
| Organizar a investigação | Consultas orientadas pelos indícios encontrados, com informações e lacunas apresentadas no dossiê |
| Fundamentar e revisar a decisão | Registro consultável da análise, das evidências e da justificativa do profissional |

&emsp;A comparação com perfis semelhantes (C1) é uma funcionalidade complementar que **não será entregue neste ciclo** (decisão D4, 29/10/2026). A EDA não identificou condições suficientes para implementar clustering com os dados disponíveis dentro do prazo. O dossiê sinalizará a indisponibilidade desta funcionalidade. O canvas de proposta de valor mantém a comparação por perfis como benefício futuro.

<a id="331-modelo-preditivo-de-risco-de-fraude"></a>
<a id="331-avaliacao-de-risco-da-transacao"></a>
### 3.3.1 Avaliação de risco da transação

&emsp;O Fio produzirá um indicador de risco para cada transação analisada. Esse indicador apoiará o profissional na avaliação do caso e poderá ser utilizado como um dos critérios para organizar a atenção entre ocorrências. Sua função é contribuir para a investigação, com a decisão sobre o tratamento da transação sob responsabilidade humana.

<a id="332-modulo-de-explicabilidade"></a>
<a id="332-explicacao-dos-fatores-de-risco"></a>
### 3.3.2 Explicação dos fatores de risco

&emsp;A solução mostrará quais características da transação mais contribuíram para a avaliação de risco. Para isso, será utilizada a técnica SHAP, que permite identificar a contribuição de cada característica para o resultado do modelo (LUNDBERG; LEE, 2017). A apresentação dessas explicações será feita em linguagem compreensível para o analista.

&emsp;Essas explicações serão apresentadas junto ao histórico e às evidências do caso, oferecendo elementos para que o analista compreenda a avaliação, identifique possíveis inconsistências e registre seu entendimento.

<a id="333-plataforma-de-investigacao"></a>
### 3.3.3 Plataforma de investigação

&emsp;A plataforma reunirá em uma interface o indicador de risco, as explicações, o histórico do associado e as informações do estabelecimento. O dossiê destacará os elementos relevantes para a análise e permitirá consultar a origem das informações utilizadas.

&emsp;O profissional poderá registrar a análise e a justificativa de sua decisão e recuperá-las posteriormente, junto às evidências consideradas. Quando uma informação não estiver disponível, essa condição será sinalizada para apoiar uma leitura adequada do caso.

<a id="334-comportamento-agentico-de-organizacao-da-investigacao"></a>
<a id="334-investigacao-orientada-pelos-indicios-do-caso"></a>
### 3.3.4 Investigação orientada pelos indícios do caso

&emsp;A organização das consultas acompanhará os indícios encontrados. Quando a transação apresentar uma diferença relevante de valor, por exemplo, a investigação poderá priorizar o histórico de compras do associado. Quando o estabelecimento concentrar indícios relevantes, a consulta poderá começar por seus eventos anteriores.

&emsp;A sequência de consultas e os elementos encontrados ficarão visíveis para o analista. Se o contexto disponível for insuficiente, o dossiê indicará as lacunas para que o profissional avalie os próximos passos.

---

<a id="34-o-que-esta-fora-do-escopo"></a>
## 3.4 O que está fora do escopo

&emsp;A primeira entrega será uma versão de demonstração, desenvolvida com os dados fictícios disponibilizados para o case. A integração com os sistemas em operação no parceiro e a implantação em produção ficam fora desta primeira versão.

&emsp;Também permanecem fora do escopo:

- **Análise financeira da implantação:** projeções de retorno sobre investimento, redução de perdas e custo-benefício de adoção.
- **Ações operacionais autônomas:** bloqueio de cartões, confirmação automática de fraude ou execução de respostas sobre a transação. A decisão e a justificativa cabem ao profissional.

---

<a id="35-o-papel-do-ibm-bob-no-desenvolvimento"></a>
## 3.5 O papel do IBM Bob no desenvolvimento

&emsp;O IBM Bob será utilizado como ferramenta de apoio à construção do projeto, incluindo análise de dados, desenvolvimento do modelo e implementação da aplicação. O cálculo do indicador de risco ficará a cargo do modelo desenvolvido para o Fio, e a plataforma organizará suas explicações e as informações do caso para o analista.

---

<a id="36-conclusao"></a>
## 3.6 Conclusão

&emsp;O escopo do Fio concentra a primeira entrega em uma experiência investigativa: receber uma transação suspeita, reunir o contexto relevante, apresentar o risco de forma compreensível e registrar a decisão humana.

&emsp;Com essa proposta, o benefício esperado é oferecer ao analista uma visão organizada do caso e apoio à construção de uma justificativa clara. As entregas se articulam à proposta de valor apresentada no [canvas](canvas-proposta-de-valor.md) e ao desafio de negócio descrito no [diagnóstico](diagnostico-problema-oportunidade.md).

---

<a id="referencias"></a>
## Referências

AMERSHI, Saleema *et al.* **Guidelines for Human-AI Interaction**. In: CHI CONFERENCE ON HUMAN FACTORS IN COMPUTING SYSTEMS, 2019, Glasgow. Proceedings [...]. New York: ACM, 2019. Artigo 3, p. 1–13. DOI: [10.1145/3290605.3300233](https://doi.org/10.1145/3290605.3300233). Disponível em: [publicação dos autores na Microsoft Research](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/). Acesso em: 27 out. 2026.

LUNDBERG, Scott M.; LEE, Su-In. **A unified approach to interpreting model predictions**. Advances in Neural Information Processing Systems, v. 30, 2017. Disponível em: [anais oficiais do NIPS 2017](https://papers.nips.cc/paper_files/paper/2017/hash/8a20a8621978632d76c43dfd28b67767-Abstract.html). Acesso em: 27 out. 2026.

SHNEIDERMAN, Ben. **Human-centered artificial intelligence: reliable, safe & trustworthy**. International Journal of Human–Computer Interaction, v. 36, n. 6, p. 495–504, 2020. DOI: [10.1080/10447318.2020.1741118](https://doi.org/10.1080/10447318.2020.1741118). Disponível também na [versão do autor](https://arxiv.org/abs/2002.04087). Acesso em: 27 out. 2026.
