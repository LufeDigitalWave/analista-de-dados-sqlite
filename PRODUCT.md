# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegado pelo usuário ao aprovar a especificação: Python 3.12, Streamlit, SQLite, pandas e Gemini (endpoint compatível com OpenAI) por padrão, com adaptador opcional para a OpenAI Responses API.

## Users

Avaliadores do desafio técnico da Franq e uma pessoa que queira explorar o banco SQLite fornecido sem escrever SQL.

## Product Purpose

Responder perguntas em português sobre o banco com dados consultados em tempo de execução. O sucesso é uma resposta verificável, com tabela ou gráfico e as consultas SQL utilizadas.

## Operating Context

Aplicação local de demonstração para o Desafio Técnico 1. O operador configura o caminho do banco e a chave da API, pergunta na interface e revisa a resposta e o SQL. O banco contém dados pessoais e fica fora do repositório.

## Capabilities and Constraints

- Um único agente descobre o schema, faz consultas SQLite somente leitura, pode corrigir SQL inválido e conclui em passos limitados.
- A interface exibe resposta, pressupostos, dados, gráfico quando apropriado e trilha operacional.
- Até 200 linhas por consulta; sem edição do banco, autenticação multiusuário ou implantação de produção.
- O código não contém consultas prontas para as perguntas do enunciado.

## Evidence on Hand

- O enunciado e o banco fornecido pelo recrutador estão disponíveis apenas localmente; o banco não deve ser publicado.
- Cinco perguntas do enunciado têm valores de referência agregados para validação.
- Não há marca visual própria ou conteúdo comercial a reproduzir.

## Product Principles

1. Evidência antes da afirmação: toda resposta factual aponta para uma consulta executada.
2. Transparência operacional sem expor raciocínio interno do modelo.
3. Privacidade e segurança no limite do banco, não só no prompt.
4. Poucos controles, com a pergunta e o resultado como centro da experiência.
