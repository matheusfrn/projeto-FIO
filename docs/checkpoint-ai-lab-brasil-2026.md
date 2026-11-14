# Checkpoint de Meio de Programa

**Recorte do projeto:** Trilha 4 (SICREDI), quinto exemplo de solução da lista. *Apoio à investigação de fraudes: plataforma que auxilia equipes especializadas a consolidar informações, evidências e histórico de eventos para acelerar análises e decisões.*

**Antes de colar:** usei **"Fio"** como nome do projeto, de "puxar o fio da meada", que é literalmente o que um investigador de fraude faz. Se o time não bater o martelo nesse nome, troque com Ctrl+F antes de enviar. Mas vale ter nome: a maioria das equipes vai escrever "nossa solução" o formulário inteiro.

---

## 1. Desafio da indústria

```
Trilha 4: Prevenção Inteligente a Fraudes e Segurança do Associado com SICREDI
```

---

## 2. Ideia preliminar e descrição

```
O Fio é um agente de investigação voltado para o analista de prevenção a fraudes do Sicredi. Não é mais um detector de fraude. É o que acontece depois do alerta, ajudando o analista a transformar uma transação suspeita em uma investigação organizada e fundamentada em evidências.

O gargalo real não está só em apontar a transação suspeita, mas no trabalho que vem logo depois. O analista recebe um alerta isolado e precisa reconstruir a história do associado por conta própria: consulta o histórico, compara com o padrão daquela pessoa, verifica se outros associados de perfil parecido sofreram algo semelhante e checa se o estabelecimento já apareceu em casos anteriores. É um trabalho de investigação manual, feito sob pressão de tempo, e é exatamente aí que a fila trava.

O Fio recebe a transação suspeita e utiliza um modelo preditivo de Redes Neurais, desenvolvido e treinado ao longo do projeto com os dados do Sicredi disponibilizados para o case, para gerar um score de risco de fraude. Com apoio do IBM Bob, a equipe vai desenvolver e aprimorar esse modelo durante o projeto, desde a análise exploratória e a preparação dos dados até o treinamento e a avaliação dos resultados.

A partir do score e das evidências disponíveis, o Fio organiza um dossiê investigativo com a linha do tempo do associado, os desvios em relação ao padrão individual e ao cluster de consumo em que ele se encaixa, os eventos relacionados e os fatores que ajudam a explicar o risco apontado pelo modelo. Com isso, o analista abre a tela com parte da investigação já estruturada e os indícios à vista, inclusive para questionar a avaliação do modelo quando necessário.

O score de risco é um apoio à investigação, não uma decisão definitiva sobre a existência de fraude. A decisão continua sendo humana. O que muda é o tempo entre o alerta e uma decisão informada, além da possibilidade de tornar as análises mais consistentes entre diferentes profissionais que examinam o mesmo caso.
```

---

## 3. Proposta de valor

```
Para o analista de prevenção a fraudes do Sicredi, que precisa avaliar alertas rapidamente sem ter todo o contexto reunido em um só lugar, o Fio entrega um dossiê investigativo que combina um score de risco gerado por um modelo de Redes Neurais com o histórico, os padrões de comportamento, os eventos relacionados e as evidências disponíveis sobre a transação.

A proposta de valor está em transformar informações dispersas em uma investigação estruturada. Em vez de precisar consultar diferentes fontes e construir manualmente uma hipótese, o analista recebe os principais indícios organizados, entende os fatores que contribuíram para o risco apontado e consegue concentrar seu tempo na análise crítica e na decisão.

A diferença em relação a um painel de BI é que o painel apresenta dados e exige que o analista saiba o que procurar. O Fio combina a avaliação preditiva do modelo com a organização das evidências e a explicação dos fatores relevantes para cada caso. Ele não substitui o julgamento profissional, mas oferece mais contexto, mais transparência e mais suporte para que a decisão seja bem fundamentada.

O que cada parte ganha com isso:

Analista: redução do trabalho manual de coleta e cruzamento de informações, investigação mais ágil e melhor compreensão dos fatores que influenciam o score de risco.

Associado: potencial redução de bloqueios indevidos e respostas mais ágeis quando houver suspeita real de fraude, contribuindo para uma experiência bancária mais segura e com menos transtornos desnecessários.

Sicredi: maior eficiência na investigação de alertas, mais consistência entre análises, melhor priorização dos casos e uma trilha de evidências que apoia a justificativa das decisões e os processos de auditoria.

No fundo, o Fio não quer apenas identificar risco. Quer reduzir o esforço necessário para compreender cada alerta, reunir evidências e chegar a uma decisão humana mais bem fundamentada.
```

---

## 4. Justificativa do uso de IA

```
Regras resolvem a parte mais simples: valor acima de X, em país Y, fora do horário habitual. Essa camada consegue identificar situações previamente definidas, mas tem limitações para reconhecer mudanças mais complexas no comportamento dos associados. Uma regra fixa tende a tratar situações parecidas da mesma forma, mesmo quando o contexto individual é completamente diferente.

O que exige IA é reconhecer padrões de comportamento e identificar desvios em relação a eles. Uma compra de R$ 4 mil em eletrônicos pode ser completamente habitual para um associado e representar uma anomalia clara para outro. O modelo precisa aprender as relações presentes nos dados para estimar o risco associado a cada situação, em vez de depender exclusivamente de limites definidos manualmente.

Para isso, o Fio vai contar com um modelo preditivo de Redes Neurais, que será desenvolvido, treinado e avaliado ao longo do projeto com os dados do Sicredi disponibilizados para o case. Esse modelo será o responsável por produzir o score de risco de fraude. A equipe vai utilizar o IBM Bob como apoio ao desenvolvimento, incluindo a análise exploratória dos dados, a preparação e a engenharia de atributos, a implementação do treinamento, a avaliação do desempenho e o refinamento do modelo.

O score não vai ser tratado como prova definitiva de fraude, mas como um indicador para apoiar a investigação. Por isso, a solução também vai buscar explicar os fatores que contribuíram para o risco estimado e relacioná-los às evidências disponíveis. Dessa forma, o analista consegue compreender os resultados, avaliar se fazem sentido no contexto do caso e contestar a indicação do modelo quando achar necessário.

O caráter agêntico do Fio está em tratar a investigação como uma sequência de etapas que pode mudar de acordo com o que é encontrado. Se o principal indício está no valor, a investigação pode priorizar o histórico de compras. Se está no estabelecimento, pode priorizar eventos associados a ele. Se vários associados do mesmo cluster foram atingidos em um período próximo, a análise pode buscar indícios de um padrão de ataque. Em vez de apenas classificar uma transação, o agente organiza a busca por evidências, sintetiza o que encontrou e encaminha o caso ao analista quando o contexto não for suficiente para sustentar uma conclusão.

Nos arquétipos do guia, somos um sintetizador de conhecimento combinado com um gestor de fronteiras entre humanos e IA. O agente reúne e organiza informações para apoiar a investigação, mas nunca bloqueia um cartão ou confirma uma fraude por conta própria.

O IBM Bob será uma ferramenta de apoio à construção da solução, não o modelo responsável por calcular o score em produção. A geração do score fica a cargo do modelo de Redes Neurais treinado pela equipe. A apresentação das explicações em linguagem natural será responsabilidade da aplicação, que vai utilizar os resultados de explicabilidade do modelo e as evidências reunidas no dossiê para apresentar ao analista uma justificativa clara e direta.
```

---

## 5. Comentários ou observações para o coach

```
Escopo que fechamos para o restante do programa.

Dentro: entendimento do parceiro e da dor do analista antifraude, canvas de proposta de valor, análise exploratória, modelo preditivo com explicabilidade, plataforma e apresentação.

Fora: análise financeira e construção de persona. Preferimos ir fundo em explicabilidade a espalhar esforço fino demais.

Duas dúvidas em que a ajuda do coach faria diferença:

1. Como validar se o dossiê é de fato útil sem ter um analista do Sicredi para olhar? Existe algum canal com o parceiro para uma validação rápida, nem que sejam 20 minutos?

2. Nos dados fictícios, qual o desbalanceamento entre fraude e não-fraude? Isso muda nossa escolha de métrica e de estratégia de treino, e preferimos decidir agora do que refazer depois.
```

---

## 6. Material de apoio (opcional)

Anexa o **canvas de proposta de valor** que já está na lista de entregáveis. Se ainda não estiver pronto, um one-pager com o fluxo transação suspeita, coleta de evidências, dossiê e decisão do analista resolve bem e é mais útil do que um slide bonito.

---

### Por que esses textos devem destacar vocês

O que quase toda equipe da Trilha 4 vai escrever: "nossa solução usa IA para detectar fraudes em cartão". É o primeiro bullet da lista de inspiração do PDF.

O recorte de vocês é o último bullet, e ele é melhor por três motivos que os textos acima deixam explícitos:

- **O usuário tem rosto.** Não é "o Sicredi", é o analista antifraude com fila na mesa. Proposta de valor só funciona quando o usuário é uma pessoa real.
- **Vocês não competem com o motor de fraude que o Sicredi já tem.** Vocês atacam o que vem depois dele, que ninguém olha.
- **A explicabilidade deixa de ser enfeite de slide e vira o produto.** Para um detector, explicabilidade é um extra. Para um dossiê, é a coisa toda.

Se precisar cortar algo por limite de caracteres, corte a lista de valor por parte na proposta de valor. Não corte o parágrafo sobre o caráter agêntico, pois é o único lugar do formulário que responde direto à pergunta que eles fizeram.
