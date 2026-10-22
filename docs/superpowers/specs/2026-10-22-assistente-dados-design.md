# Assistente de Dados SQLite - especificação

Data: 22/10/2026. Decisão aprovada pelo usuário: implementar o Desafio Técnico 1 com um único agente e interface Streamlit.

## Objetivo e escopo

Uma pergunta em português recebe uma resposta baseada em consultas ao banco SQLite fornecido, com tabela ou gráfico apropriado e um registro legível dos passos e SQL executados. O código de produção não contém consultas específicas das cinco perguntas do enunciado. A aplicação roda localmente com Python 3.12, `OPENAI_API_KEY` e o caminho do banco configurados por ambiente.

O outro desafio fica como esboço arquitetural no README para conversa na entrevista. Não há OCR, edição do banco, autenticação de múltiplos usuários nem implantação em produção neste MVP.

## Fluxo

1. A interface recebe a pergunta e a preferência opcional de visualização.
2. O agente pede o schema real por uma ferramenta que usa metadados do SQLite, incluindo tabelas, colunas e chaves estrangeiras.
3. O modelo `gpt-4.1` decide quais consultas são necessárias. Cada chamada da ferramenta SQL é validada e executada em conexão somente leitura. Resultados ou erros voltam ao modelo para nova decisão.
4. O agente pode fazer mais de uma consulta e corrigir SQL inválido, dentro de limites finitos de passos e falhas.
5. A resposta final referencia os resultados obtidos. A interface apresenta narrativa, dados, visualização e rastreio operacional (objetivo de cada consulta, SQL, contagem de linhas e erros). Não exibe raciocínio interno do modelo.

## Interfaces e decisões

- `SQLiteReadOnly.inspect_schema() -> dict`: schema descoberto em tempo de execução, sem linhas pessoais.
- `SQLiteReadOnly.run_sql(sql: str) -> QueryResult`: apenas uma instrução `SELECT` ou `WITH`, conexão `mode=ro`, autorizador SQLite, limite de linhas e tempo. Falha é devolvida como erro controlado.
- `OpenAIModel.next_turn(history: list) -> ModelTurn`: Responses API com function calling estrito, `store=False`, `parallel_tool_calls=False` e modelo configurável. O histórico da pergunta fica em memória apenas durante a execução.
- `DataAnalyst.ask(question: str) -> Answer`: coordena ferramentas, limites, autocorreção e trilha. Recebe banco e modelo por injeção para testes sem API paga.
- A função de visualização só usa colunas do resultado efetivamente executado. A sugestão do modelo pode ser substituída pelo usuário; formato inválido recua para tabela.

O prompt é versionado. Ele orienta o agente a distinguir clientes de eventos, reclamações de outros contatos, períodos relativos da data máxima disponível e métricas transacionais de colunas resumidas. Se a pergunta for ambígua, a resposta registra a interpretação usada ou pede esclarecimento.

## Segurança e privacidade

O banco fornecido contém dados pessoais e não entra no Git. `.env` e arquivos SQLite são ignorados antes da inicialização do repositório. O autorizador impede escrita e bloqueia leitura direta de colunas tipicamente pessoais, como nome e e-mail. O modelo recebe somente a pergunta, o schema e até 200 linhas por consulta; `store=False` desativa armazenamento do objeto de resposta na API. A chamada ao provedor ainda transmite os dados retornados pela ferramenta, portanto o uso deve ocorrer apenas com base autorizada.

## Critérios verificáveis

1. Testes com banco sintético provam introspecção de schema, consultas, bloqueio de escrita e limites.
2. Testes com modelo simulado provam múltiplas consultas, correção após erro e encerramento finito.
3. As cinco perguntas do enunciado têm valores de referência locais e interpretação documentada; pelo menos uma execução real do agente é confrontada com esse oráculo quando a API estiver disponível.
4. A interface inicia, recebe pergunta, mostra resposta, tabela/gráfico e SQL no navegador.
5. README permite instalar e executar em três etapas; dados e segredos ficam fora do conjunto versionado.

## Riscos e limites

- O modelo pode escolher SQL semanticamente errado apesar de válido. A tabela e o SQL permanecem visíveis para revisão humana; o README descreve a limitação e os exemplos de referência.
- Um resultado truncado não sustenta totalização; o prompt e a interface o sinalizam.
- O banco do desafio estático não representa concorrência, atualização contínua ou controles de acesso de produção.
- A publicação no GitHub é etapa externa separada, após conferir o repositório local e receber autorização explícita para torná-lo público.
