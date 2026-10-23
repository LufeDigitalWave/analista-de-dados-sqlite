# Fechamento verificável do Desafio 1 — especificação

Data: 22/10/2026. Esta especificação complementa a [estabilização anterior](2026-10-22-estabilizacao-case-design.md); não reabre suas tarefas concluídas nem transforma o protótipo local em produto de produção. O prazo de submissão comunicado por e-mail é 23/10/2026.

## Problema e evidência disponível

O Desafio 1 está implementado e publicado. A suíte offline foi repetida nesta etapa com **337 testes aprovados**. Na última avaliação real documentada, os cinco agregados coincidiram com os oráculos, mas os cinco casos terminaram em `REVIEW` por redação não comprovada automaticamente. Houve um `FAIL` de agregado na pergunta 2 em execução intermediária, seguido de coincidência numa execução posterior. Os relatórios preservaram status, contagens e capturas parciais, não a resposta final e o SQL selecionado de cada uma das cinco execuções; por isso não permitem concluir retrospectivamente a revisão humana.

Uma reprodução sintética adicional isolou um falso `REVIEW` do avaliador: com a mesma tabela de 17 clientes e o mesmo SQL, “17 clientes em 2024” passa, enquanto “17 clientes entre 01/01/2024 e 31/12/2024” vai a `REVIEW` porque dia e mês são lidos como contagens adicionais. Isso **não prova** que tenha causado algum dos cinco `REVIEW` históricos. Corrigir esse caso não autoriza chamar a rodada histórica de aprovada.

## Objetivo e prioridade

Produzir evidência suficiente para revisar manualmente as cinco perguntas antes da submissão, mantendo separados o veredito automático, a conclusão humana e a variabilidade de uma nova execução. Uma resposta só será declarada confirmada se pergunta, interpretação, SQL selecionado, tabela e redação concordarem. Caso contrário, registrar `INCORRETA` ou `INDETERMINADA` com motivo e corrigir apenas falha reproduzida.

O fechamento de evidência é prioridade sobre melhorias opcionais porque a revisão pendente está registrada no [checkpoint público](../../STATUS-ENTREGA.md) e o prazo é 23/10. Snapshot entre consultas, autenticação, política PII genérica e redesenho do agente não são pré-requisitos demonstrados para o banco estático deste case.

## Alternativas e decisão

1. **Capturas manuais da interface:** menor mudança de código, mas SQL, texto e tabela ficam dispersos; uma nova execução pode divergir sem trilha comparável.
2. **Registro privado e estruturado da avaliação existente — escolhido:** uma opção explícita do avaliador gera, numa única rodada, um pacote local com um registro por caso e a evidência necessária; depois há revisão humana e correção TDD somente para defeitos demonstrados. Mantém o fluxo de produção e o avaliador conservador.
3. **Verificador formal de afirmações:** contrato factual estruturado entre agente e resultado poderia aumentar garantias, mas alteraria ferramentas, prompt e interface perto do prazo. Fica para uma etapa posterior, se houver necessidade comprovada.

O fluxo é: `pergunta → agente/SQLite/API autorizados → assess_case → pacote privado ignorado pelo Git → matriz de revisão humana → correção reproduzida, se necessária → status público sem dados brutos`.

## Contratos do pacote privado

- A CLI de `evals.live_eval` mantém por padrão seu comportamento atual: imprime apenas status, contagens agregadas e identificadores redigidos, sem gravar resposta, SQL literal, chave ou linhas de dados. Uma opção explícita `--private-review` cria **um arquivo novo** no `work/private-review/` da tarefa, fora de `outputs/` e da árvore Git neste workspace Codex. Em clone convencional sem a pasta `outputs/`, usa `<repo>/work/private-review/`, já ignorado pelo Git. Não aceita destino arbitrário, não sobrescreve pacote anterior e não imprime seu conteúdo no terminal.
- O pacote identifica versão do formato, instante UTC, commit Git e árvore limpa **antes e depois**, modelo configurado, casos selecionados/tentados/concluídos e hash SHA-256 do banco **antes e depois**, somente dentro do arquivo privado, sem caminho do banco nem chave. Mudança ou indisponibilidade dessas verificações entra em `integrity_issues`, separado de `error_code` da execução, e impede considerar a amostra reproduzível. Cada registro contém número/pergunta, veredito e motivos do avaliador, `aggregate_match`, texto/pressupostos da resposta, SQL selecionado, SQLs bem-sucedidos, gráfico escolhido e colunas/linhas **apenas quando o agregado foi reconhecido como coincidente**. Caso contrário, registra só contagem de linhas e motivo; nunca grava objetos brutos do provedor, mensagens de exceção ou resultados intermediários. No caso interrompido por erro tratado, campos de resposta indisponíveis são `null` e há código categórico `execution_<tipo>`, sem mensagem bruta; encerramento forçado do processo não promete pacote. Falha de pré-verificação (banco, chave ou oráculos indisponíveis) não cria pacote.
- A gravação deve resolver no `work/` previsto e, neste workspace, permanecer fora de `outputs/`; no fallback de clone comum, verificar também `git check-ignore work/private-review`. Falha de gravação não pode ser apresentada como avaliação completa. O pacote é imutável após criado. A revisão humana fica em **outro arquivo privado**, vinculado ao nome e hash do pacote, para não alterar a evidência original. Nenhum dos dois vai para commit, ZIP, relatório público ou e-mail. Tratar ambos com a mesma confidencialidade do banco: agregados pequenos, SQL e texto do modelo podem conter informação identificável e **não são declarados anonimizados**. Se a inspeção encontrar dado pessoal, não divulgar o pacote e registrar a restrição na revisão, sem apagar ou sobrescrever automaticamente a evidência.
- A execução real, se necessária pela ausência de evidência anterior, usa somente o banco local e a autorização já concedida para pergunta, esquema, SQL e resultados agregados na API; **não envia o arquivo `.db`**. Limitar a uma rodada das cinco perguntas antes de qualquer novo teste pago. Para chamar essa amostra de reproduzível, primeiro registrar o código em commit local e exigir árvore limpa no início **e no fim** da rodada, com o mesmo commit; commit mais `dirty=true` não identifica sozinho todas as mudanças executadas. Nova rodada é nova amostra, não reconstrução do texto da rodada histórica.

## Revisão das cinco perguntas

Para cada caso, registrar no arquivo privado de revisão uma decisão humana `CONFIRMADA`, `INCORRETA` ou `INDETERMINADA`, com justificativa vinculada ao SQL, à tabela e à frase relevante. O status automático `PASS`/`REVIEW`/`FAIL` permanece separado e nunca é reescrito para agradar o gate.

| Caso | Verificação semântica necessária |
| --- | --- |
| Q1 | Maio de 2025, canal App, clientes distintos por estado, cinco posições e empate final admissível |
| Q2 | Interação verdadeira, WhatsApp, envios de 2024 e clientes distintos; contagem de interações não substitui pessoas |
| Q3 | Compras divididas pelos compradores distintos da própria categoria, ordem decrescente e denominador explicado |
| Q4 | Apenas contatos de reclamação com `resolvido=0`; `NULL` não entra por omissão, canais e contagens corretos |
| Q5 | Janela 22/07/2024–22/07/2025 ancorada na maior data, meses/canais completos, três séries e redação compatível com a tabela |

Se uma diferença de interpretação puder ser legítima, explicitá-la e manter `INDETERMINADA` até haver justificativa verificável. Não executar repetidamente o modelo para escolher só a melhor amostra. O `FAIL` anterior da Q2 não ganha causa específica sem o registro perdido; um novo erro semelhante exige diagnóstico próprio.

## Correção estreita já reproduzida

Adicionar teste sintético RED para a frase com datas completas na Q2 antes de alterar `_text_integer_verdict`. O resultado esperado é `PASS` quando o total de clientes, SQL e período são corretos e a frase expressa um intervalo explícito. Duas datas apenas listadas como dias isolados ou uma negação do intervalo não comprovam o ano inteiro e permanecem em `REVIEW`. Controles com ano incorreto, outra contagem (“17 clientes, dos quais 3...”), quantidade errada, número por extenso ou frase ambígua **não** podem virar `PASS` por simples descarte de números. Depois da correção mínima, executar GREEN e a suíte completa. Não ampliar regras das outras perguntas sem contraexemplo.

## Critérios de aceite e publicação

1. Testes sintéticos demonstram a opção de captura, ausência de gravação por padrão, não sobrescrita e saída pública redigida; a opção não oferece destino arbitrário e o caminho resolvido permanece no `work/` previsto, fora de `outputs/` quando aplicável. A regressão de data da Q2 passa sem promover os controles ambíguos a `PASS`.
2. Se houver rodada real, registrar cinco casos ou falha explícita, conferir hash do banco antes/depois, manter o pacote fora do repositório ou ignorado no fallback e revisar localmente se texto/SQL e tabela podem ser lidos sem dados pessoais por linha. O resultado real é relatado tal como ocorreu, inclusive `REVIEW`/`FAIL`.
3. A matriz privada, separada e vinculada ao pacote imutável, tem uma decisão justificada para Q1–Q5 ou marca precisamente a evidência faltante. Se houver informação pessoal no texto/SQL, o pacote continua privado e a matriz registra essa restrição. Nenhuma frase pública afirma “5/5 aprovado” apenas porque agregados coincidiram ou porque a heurística mudou.
4. `pytest`, Ruff, `pip check`, verificação do diff e scan local de segredos passam na árvore final. Como é um projeto web, o smoke reproduzível de Playwright é repetido antes de afirmar que o aplicativo continua utilizável; nenhuma mudança de UI é planejada.
5. O [README](../../../README.md) e o [checkpoint público](../../STATUS-ENTREGA.md) refletem o estado verdadeiro. Só código, testes e documentação publicável entram no Git/ZIP; conferir `git ls-files` e o manifesto do ZIP, além de `git check-ignore` no fallback, para impedir inclusão forçada de `work/`. O pacote privado fica fora. O usuário, não esta tarefa, decide o envio do link à Franq.

## Evidência surgida na execução desta spec

A primeira rodada real no commit `6a9d0eb` concluiu Q1–Q5 com commit limpo e hash do banco estáveis. O resultado automático foi Q1/Q3/Q4 `REVIEW`, Q2 `FAIL` e Q5 `PASS`. A conferência técnica assistida reconciliou pergunta, SQL, tabela e redação de Q1, Q3, Q4 e Q5, mas **não é a validação humana do candidato**. Na Q2, o modelo substituiu o ano civil explicitamente pedido por uma janela móvel de doze meses; SQL e resposta ficaram incorretos. O pacote e a matriz permanecem privados e a amostra reprovada não será alterada ou reclassificada.

O reparo estreito subsequente é explicitar no prompt, de forma genérica, a prioridade de um ano civil nomeado sobre regras de período relativo, com regressão offline. Um teste do texto do prompt prova somente o contrato escrito, não a obediência do modelo; eventual reavaliação paga dirigida da Q2 exige decisão/autorização separada e será registrada como **nova amostra**, sem substituir a primeira.

## Limites

Não implementar o Desafio 2, adicionar LangChain/LangGraph, fazer deploy, enviar e-mail, publicar banco/chave/respostas brutas nem executar Strix nesta rodada. Uma avaliação individual não demonstra estabilidade estatística ou equivalência geral entre pergunta, SQL e texto. A revisão humana reduz a incerteza desta amostra; qualquer uso posterior com dados sensíveis exige controles próprios.
