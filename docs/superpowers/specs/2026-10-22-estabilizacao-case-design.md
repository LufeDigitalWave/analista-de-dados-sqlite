# Estabilização do Desafio Técnico 1 — especificação de entrega

Data: 22/10/2026. Escopo autorizado: continuar as correções do protótipo já publicado, sem executar Strix nesta rodada. Esta especificação complementa [a especificação original](2026-10-22-assistente-dados-design.md); não troca a escolha do Desafio 1 nem a arquitetura de um agente, SQLite e Streamlit.

## Alvo e requisitos do enunciado

Entregar até 23/10/2026 o código-fonte completo em repositório público, com README de execução, fluxo/arquitetura, consultas testadas e melhorias futuras. O assistente deve descobrir o esquema em tempo de execução, responder perguntas em português com consultas potencialmente múltiplas, recuperar-se de SQL inválido e mostrar resposta, tabela ou gráfico e a trilha de SQL. O Desafio 2 exige apenas um esboço de arquitetura para a entrevista, já presente no README; não se implementam os 50 PDFs.

O produto continua uma **demonstração local**, não um serviço para dados sensíveis em produção. O banco fornecido e a chave da API não são versionados. O código de produção não incorpora SQL pronto para as cinco perguntas; os oráculos permanecem em `evals/`.

## Escopo total do case escolhido

| Frente | Entrega exigida nesta submissão | Evidência de aceite |
| --- | --- | --- |
| Acesso aos dados | Descobrir tabelas, colunas e relações do SQLite em tempo de execução; consultar somente leitura, com limites e privacidade | Testes sintéticos de introspecção, política, timeout, truncamento e snapshots |
| Raciocínio operacional | Uma pergunta em português aciona esquema, uma ou mais consultas SQL, correção de erros e resposta ligada à última evidência | Testes do agente e trilha de objetivo, SQL, resultado/erro e consulta selecionada |
| Apresentação | Mostrar resposta, tabela, gráfico quando pertinente e troca manual de visualização | Testes de renderização e Playwright em desktop/celular |
| Cinco perguntas do enunciado | Responder os exemplos sem hardcode na produção; manter oráculos SQL separados para conferir métricas e períodos | Avaliador offline e rodada real autorizada, registrando `PASS`/`REVIEW`/`FAIL` sem ocultar ressalvas |
| Entrega técnica | Repositório público de código, README executável, arquitetura/fluxo, testes, limitações e pacote local equivalente | Revisão de diff/segredos, gates offline, commit e ZIP da mesma árvore |
| Desafio 2 | Apenas esboço de arquitetura para discussão, não implementação dos 50 PDFs | Seção própria no README, distinguida do Desafio 1 implementado |

O plano histórico da construção inicial registra a implementação base; o [plano de estabilização](../plans/2026-10-22-estabilizacao-case.md) é o roteiro ativo para fechar esta entrega. Não há deploy público do aplicativo: "público" refere-se somente ao código-fonte sem banco/chave.

## Decisão de desenho

Foram consideradas três rotas:

1. **Correção focal (escolhida):** reforçar o protocolo de evidência existente, retirar o cache de autorização e alinhar a interface móvel, sem nova dependência ou nova camada de agentes. Cabe no prazo e mantém o fluxo já testado.
2. **Redesenho do motor de evidência:** representar formalmente todas as afirmações, provas e predicados SQL. Ofereceria garantias mais fortes, mas mudaria o contrato do agente e a avaliação inteira perto da entrega.
3. **Apenas documentar os riscos:** preservaria o código, mas deixaria os contraexemplos reproduzidos em aberto.

A escolha focal **não prova equivalência semântica geral** entre texto livre e SQL. Ela fecha os caminhos concretos reproduzidos e mantém a revisão humana da trilha como requisito operacional.

## Contratos de correção

### E1 — Resposta ligada à evidência

- `query_number=0` é somente esclarecimento: a aplicação exibe uma pergunta neutra controlada pelo servidor, sem reutilizar texto factual, pressupostos ou sugestão de gráfico vindos do modelo.
- A consulta que fornece `Answer.data` é a última consulta SQL bem-sucedida. Consultas intermediárias continuam permitidas; se uma sondagem vier depois da agregação principal, o agente deve executar a agregação novamente antes de concluir.
- Para resultado vazio ou zero, uma sondagem anterior só conta como verificação se examinar, sem filtro redutor, a mesma dimensão categórica, flag binária aprovada ou coluna de data usada no filtro da consulta final. Uma distribuição por outra coluna da mesma tabela não basta; a dimensão agrupada ou o extremo `MIN/MAX` precisa ser projetado, para que seus valores estejam de fato visíveis. A sonda não pode usar `JOIN`, pois o predicado `ON` também pode reduzir a distribuição mesmo sem `WHERE`. Dúvidas falham de forma controlada, sem declaração de ausência.
- Essas regras ficam em código e testes sintéticos; o prompt descreve o contrato, mas não é a única barreira.

### D1 — Autorização SQLite no mesmo estado consultado

- `run_sql` abre uma conexão somente leitura, inicia uma transação de leitura e, nela, descobre o esquema/valores aprovados, valida a política SQL e executa a consulta. A autorização não reutiliza metadados em cache de chamada anterior.
- `inspect_schema` continua disponível como ferramenta de descoberta e devolve dados limitados, mas sua resposta não substitui a inspeção feita por `run_sql` na própria conexão.
- Flags `resolvido`/`interagiu` só podem ser usadas em filtro ou virar dimensões agregadas de uma sonda se a inspeção do mesmo snapshot confirmar declaração `INTEGER` ou `BOOLEAN` exato (que possui afinidade SQLite `NUMERIC`), nome explicitamente permitido e valores efetivos exclusivamente inteiros `0`, `1` ou `NULL`. `NUMERIC` genérico e texto `'0'`/`'1'` não bastam. O esquema publica apenas a aprovação, não linhas nem contagens de flag. Se houver valor fora do domínio, a leitura dessa coluna continua bloqueada.
- Preservar `mode=ro`, `query_only`, autorizador SQLite, limites de tempo/tamanho/linhas e a lista restritiva de campos categóricos.
- Substituição do arquivo por outro de tamanho/mtime idênticos e escrita concorrente via WAL não podem fazer a política validar um estado e executar em outro.

### S1 — Semântica de reclamação não resolvida

- Na pergunta de referência 4, “não resolvida” significa `resolvido = 0`; `NULL` é estado desconhecido e fica fora da contagem, salvo pedido explícito em contrário.
- O prompt orienta essa interpretação. A avaliação marca como incorreta a ampliação simples comprovada por `resolvido = 0 OR resolvido IS NULL` **com `COUNT(*)` por canal**, mesmo quando a base anexa não contém nulos e os números coincidem. Expressões SQL que mencionam `IS NULL` mas podem excluir esses registros — por exemplo, uma negação externa ou outra condição `IS NOT NULL` — não podem receber `FAIL` por essa heurística. O mesmo vale para `COUNT(resolvido)`, que ignora nulos na contagem e pode coincidir com o oráculo; quando a equivalência não é demonstrada automaticamente, ficam em `REVIEW`.
- A pergunta 5 pede **tendência**. Seu total de referência (97) deve ser conferido pela comparação das 38 linhas da série com o oráculo; não é obrigatório somar uma métrica genérica automaticamente na interface.
- Na pergunta 5, uma afirmação numérica no texto só recebe veredito automático se o avaliador conseguir vinculá-la com segurança ao total ou ao canal e período correspondentes. Valores fracionários para contagens, quantidades não interpretadas, sintaxe ambígua e números de um mês não podem ser comparados como se fossem totais anuais do canal: encaminhar a `REVIEW` quando a equivalência não estiver demonstrada. A série SQL continua sendo o oráculo principal.
- Nas perguntas 1, 2 e 4, ver apenas os literais certos no SQL não prova o predicado: `NOT` em filtro essencial ou `canal <> 'App'` na primeira pergunta exige `REVIEW` mesmo que os agregados coincidam. Contagem textual que soma dois canais ou recorta um período subanual também não é atribuída a um único canal/ano.

### U1 — Interface móvel legível

- A barra lateral usa o comportamento `auto` do Streamlit: fechada no contexto móvel novo, visível no desktop.
- Comparações usam barras horizontais com altura adequada ao número de categorias; a tabela original permanece integral.
- No gráfico de linha, eixos mensais ISO (`AAAA-MM`) passam a data apenas no *frame do gráfico*, preservando os textos da tabela. Uma legenda curta indica explicitamente o primeiro e o último mês/ano, pois o eixo estreito do gráfico nativo ainda pode rotular ambos os julhos apenas como “July”; não depender de hover em tela de toque.
- A hierarquia de títulos passa de H1 para H2, e os gráficos recebem descrição acessível compatível com os dados. Não aplicar CSS frágil para alterar o botão nativo de 28 px do Streamlit.
- O parâmetro `alt` dos gráficos nativos requer Streamlit 1.65.0 ou superior ([notas oficiais de versão](https://docs.streamlit.io/develop/quick-reference/release-notes/2026)); ajustar o mínimo declarado em `pyproject.toml` à API realmente usada.
- Desativar a coleta de estatísticas de uso do navegador na configuração local do Streamlit. Isso reduz chamadas externas observadas; não constitui prova de que toda a aplicação é livre de telemetria.

## Escopo de execução e ordem

| Pacote | Prioridade | Entrega independente | Skills aplicáveis |
| --- | --- | --- | --- |
| 1. Evidência e semântica | Bloqueante | Testes RED/GREEN para três contraexemplos, regra de `NULL`, prompt e avaliador | `systematic-debugging`, `receiving-code-review`, `test-driven-development`, `task-impl`, `prompt-engineering-patterns` |
| 2. SQLite | Bloqueante | Sem cache de autorização; mesma conexão/transação; regressões de arquivo substituído/WAL | `systematic-debugging`, `test-driven-development`, `task-impl`, `code-reviewer` |
| 3. Móvel e privacidade de navegador | Importante | Sidebar, gráficos, títulos, descrição e configuração; Playwright em 390/1280 px | `impeccable` (`adapt`), `ui-ux-pro-max`, `webapp-testing`, `playwright-cli` |
| 4. Fechamento | Bloqueante | README, suíte, lint, avaliação real, revisão de diff/segredos, repositório e pacote coerentes | `writing-plans`, `verification-before-completion`, `code-reviewer`, `secret-scanning` |

`using-superpowers`, `task-observer`, `pdf` e `brainstorming` orientam a preparação; `codebase-design` delimita a fronteira agente/SQLite. O usuário dispensou **Strix** para este case; ele não integra o gate. Context7 não estava disponível neste host; qualquer detalhe de Streamlit dependente de versão será conferido na instalação local e no Playwright.

## Critérios de aceite

1. Testes sintéticos demonstram que os contraexemplos de E1, inclusive uma sonda reduzida por `JOIN ... ON` ou uma distribuição sem projetar seus valores, deixam de produzir resposta factual; distribuições válidas de flags permitem um zero verdadeiro sem leitura de linhas individuais.
2. O teste de troca por banco de mesmo tamanho/mtime e o teste de mudança WAL não expõem categoria que uma inspeção atual recusaria. A consulta segue somente leitura.
3. Um caso com `resolvido=NULL` diferencia a definição de `resolvido=0`; o avaliador reprova a ampliação simples da pergunta 4 sem reprovar falsamente exclusões equivalentes que exigem revisão. Na pergunta 5, números por canal, decimais e menções a meses não recebem `PASS` ou `FAIL` indevidos por serem confundidos com totais globais.
4. `pytest`, `ruff check` e `ruff format --check` passam na árvore final. No Windows restrito, `pytest` usa `--basetemp` dentro da área de trabalho.
5. Playwright, em contexto limpo, confirma página inicial utilizável a 390 px e sidebar visível a 1280 px; gráficos das perguntas 1 e 5 ficam legíveis, com período explícito no gráfico mensal, sem perder tabela, SQL nem troca manual de visualização.
6. A avaliação opcional com o banco anexo e a API autorizada compara as cinco perguntas aos oráculos e relata `PASS`, `REVIEW` e `FAIL` sem mascarar ressalvas; não publica banco, chave, SQL literal ou dados pessoais.
7. Antes de publicar a atualização, conferir diff integral, arquivos rastreados e padrões de segredo. README descreve limites reais e o esboço arquitetural do Desafio 2.

## Fora do escopo e riscos restantes

- Não implementar Desafio 2, autenticação, deploy público do aplicativo, controles multiusuário, certificação WCAG, resistência a toda prompt injection ou verificador semântico formal.
- `category_fields` com nomes fora da lista categórica interna permanece limitado por segurança; documentar a necessidade de revisão de política antes de portar para outro banco.
- O alvo de toque do botão nativo de sidebar ainda pode ser menor que 44 px; a melhoria aqui é não exigir esse toque para enxergar o conteúdo inicial.
- Resultados do modelo podem variar entre execuções. A trilha SQL e os oráculos permitem avaliação, não garantem acerto em toda pergunta.
- A sonda de zero comprova que a dimensão foi observada; ela não prova que o predicado final corresponde ao enunciado em linguagem natural. Um filtro adicional ou contraditório pode produzir zero indevido; a revisão humana do SQL continua obrigatória para decisões reais.
- A transação SQLite é consistente por chamada de `run_sql`, não pela pergunta inteira. Uma escrita externa entre a sonda de zero e a consulta final pode deixar a sonda obsoleta; este case usa o anexo estático, mas não oferece garantia de snapshot multi-consulta para uma base mutável.
