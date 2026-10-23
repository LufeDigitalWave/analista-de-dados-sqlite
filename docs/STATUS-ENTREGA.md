# Estado da entrega — Desafio Técnico 1

Atualizado em 23/10/2026. Este é o checkpoint público; a [especificação](superpowers/specs/2026-10-22-estabilizacao-case-design.md), o [plano de tarefas e skills](superpowers/plans/2026-10-22-estabilizacao-case.md) e o [README](../README.md) detalham escopo, execução e arquitetura.

## Escopo escolhido

O enunciado pede a implementação completa de **um** dos dois desafios e apenas um esboço arquitetural do outro. Este repositório implementa o Desafio 1: assistente em Python para perguntas de negócio sobre SQLite, com descoberta dinâmica do esquema, consultas potencialmente múltiplas, recuperação de SQL inválido, resposta e tabela/gráfico, além da trilha das consultas. O Desafio 2 não foi implementado; seu esboço está no README. LangChain/LangGraph eram sugestões de stack, não requisitos obrigatórios.

O código usa SQLite somente leitura, OpenAI Responses API com function calling e Streamlit. Os cinco SQLs de referência ficam em `evals/`, separados do código do agente. O banco fornecido e a chave de API não integram o Git. Não há deploy público do aplicativo.

## Evidência atual

| Frente | Evidência verificada |
| --- | --- |
| Correção e segurança local | Regras para resposta ligada à última consulta, sondagem conservadora de zero, política SQL e validação/execução no mesmo snapshot SQLite; testes de substituição de arquivo, WAL, flags e erros |
| Testes offline | 373 testes aprovados (1 ignorado: symlink no Windows); Ruff check/format, `pip check` e `git diff --check` aprovados na última validação da árvore de código |
| Navegador | Playwright percorreu as cinco perguntas; resposta e tabela em todas, gráficos nas perguntas 1 e 5, contexto móvel de 390 px sem transbordamento; zero erros de página e requisições externas observadas no navegador. O backend chamou a API OpenAI nos testes reais autorizados. |
| Banco e API autorizados | Uma rodada integral anterior com `gpt-4.1` teve os cinco agregados coincidindo, com cinco `REVIEW` por texto não comprovado automaticamente. **A rodada mais recente com `gpt-4.1` (Q1–Q5) teve Q1/Q3/Q4 `REVIEW`, Q5 `PASS` e Q2 `FAIL`:** o modelo trocou o ano civil pedido ("em 2024") por uma janela móvel de 12 meses e o SQL e a resposta saíram incorretos. Não se demonstrou estabilidade 5/5. |
| Q2 após o reparo | O prompt passou a dar prioridade a ano civil nomeado sobre período relativo (teste de regressão offline). Uma nova rodada com `gpt-4.1` não pôde ser feita: a API retornou `429 credit_balance_exhausted` antes de qualquer consulta, então essa tentativa não testa o reparo. Duas amostras com outro modelo (Gemini `gemini-3-flash-preview`, só para a Q2) tiveram o agregado coincidindo com o oráculo (17): `REVIEW` na primeira e `PASS` na segunda, com filtro do ano civil de 2024. Isso é indício do efeito do prompt, **não** validação do `gpt-4.1` usado pelo produto. |
| Publicação | Repositório público contém código e documentação, sem o banco, `.env` real ou chave; a revisão de segurança foi determinística, sem Strix nesta correção por decisão do usuário |
| Conferência pela UI (Gemini) | Em 23/10/2026, as cinco perguntas foram feitas no Streamlit via `playwright-cli`, com `gemini-3.1-flash-lite` (não o `gpt-4.1`). Q1–Q4 coincidiram com os oráculos na primeira tentativa, com filtros corretos na trilha SQL. A Q5 falhou na primeira tentativa (`GROUP BY 1, 2` recusado três vezes pela política até o limite de tentativas) e passou na segunda, com 38 linhas e período 07/2024–07/2025. Sem erros de console. Conferência assistida, não é a revisão humana nem validação do modelo do produto. |

## Próximos passos e limites

1. **Antes da submissão:** conferir manualmente as cinco respostas contra pergunta, SQL, filtros, período, denominador e tabela, em especial a Q2 (ano civil de 2024) e, se houver crédito, repetir só a Q2 com `gpt-4.1` como amostra nova. O candidato ainda precisa enviar o link do repositório à Franq; este projeto não enviou e-mail.
2. **Melhoria opcional:** aumentar a estabilidade do modelo e a capacidade de verificação semântica. Uma sonda de zero observa uma dimensão, mas não prova equivalência geral entre pergunta e predicados SQL. Cada consulta tem seu próprio snapshot, não um snapshot da pergunta inteira.
3. **Fora do protótipo:** implementação dos 50 PDFs do Desafio 2, autenticação, deploy público do app, classificação geral de dados pessoais, anonimato de grupos pequenos e certificação de acessibilidade. A política de categorias e o botão nativo da sidebar têm limites documentados.

Não publique o banco, segredos, linhas pessoais ou logs brutos da API. Para continuar o desenvolvimento, use o plano de estabilização e repita testes, revisão do diff e avaliação proporcional ao risco.
