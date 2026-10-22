# Assistente de dados SQLite

Implementação do **Desafio Técnico 1**: um assistente que recebe perguntas em português, descobre o esquema do banco em tempo de execução, consulta SQLite e apresenta resposta, dados, gráfico quando pertinente e as consultas executadas. Não há SQL pronto para as perguntas do enunciado no código da aplicação.

## Executar

Requer Python 3.12 e uma chave de API com acesso ao modelo configurado. No PowerShell, na raiz deste repositório:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

$env:DATABASE_PATH = 'C:\caminho\para\anexo_desafio_1.db'
$env:OPENAI_API_KEY = '<sua-chave>'
python -m streamlit run app.py --server.address 127.0.0.1
```

Em Linux/macOS, o equivalente é:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
export DATABASE_PATH=/caminho/para/anexo_desafio_1.db
export OPENAI_API_KEY='<sua-chave>'
python -m streamlit run app.py --server.address 127.0.0.1
```

Abra a URL local indicada pelo Streamlit. O servidor fica restrito ao loopback (`127.0.0.1`); não o exponha na rede sem autenticação e revisão de segurança. O arquivo [.env.example](.env.example) lista as opções, mas não é lido automaticamente. `DATABASE_PATH` aceita um caminho absoluto ou relativo ao projeto; sem configuração, procura `data/anexo_desafio_1.db`. O banco e a chave não devem ser adicionados ao Git.

O modelo padrão é `gpt-4.1`; defina `OPENAI_MODEL` se precisar usar outro modelo compatível com [Responses API e function calling](https://developers.openai.com/api/docs/guides/function-calling). A [página oficial do GPT‑4.1](https://developers.openai.com/api/docs/models/gpt-4.1) deve ser consultada para disponibilidade atual.
Cada solicitação ao provedor tem timeout de 30 segundos e no máximo uma repetição automática; o agente limita o número de rodadas.

## Como funciona

```text
Pergunta → agente (Responses API)
            ├─ inspect_schema → metadados reais do SQLite
            ├─ run_sql        → executor somente leitura → resultado ou erro
            └─ finish_answer → resposta + índice da consulta de evidência
         → Streamlit: texto + tabela/gráfico + trilha de SQL
```

Há **um agente e três ferramentas**, sem LangChain/LangGraph: para este escopo, o [function calling nativo](https://developers.openai.com/api/docs/guides/function-calling) mantém o fluxo curto e testável. O modelo não recebe o esquema fixo no prompt; deve descobri-lo em cada pergunta. Se uma consulta falhar, recebe o erro controlado e pode tentar corrigi-la. Pode executar consultas intermediárias antes de concluir. A conclusão escolhe qual consulta bem-sucedida sustenta a tabela ou o gráfico; o aplicativo não exibe pensamento privado do modelo, apenas passos operacionais.

O executor abre o arquivo em `mode=ro`, ativa `query_only`, aceita uma única instrução `SELECT`/`WITH`, usa autorizador SQLite, bloqueia acesso direto a colunas pessoais conhecidas e interrompe consultas demoradas. Por padrão: 200 linhas e 128 KiB de resultado por consulta, 3 segundos por SQL, 8 rodadas do agente, 2 erros SQL, 4.000 caracteres por pergunta e limite SQLite de 64 KiB para valores/texto. A camada de visualização valida as colunas e o eixo numérico antes de desenhar o gráfico; a adequação semântica de barras ou linha ainda depende do agente e da revisão humana. A tabela continua disponível. Se o resultado estiver truncado, a interface avisa e não o transforma em gráfico.

## Exemplos e avaliação

Perguntas verificadas contra o banco fornecido, com definições em [evals/reference_questions.md](evals/reference_questions.md) e SQL independente em [evals/reference_queries.sql](evals/reference_queries.sql):

| Pergunta | Referência agregada |
| --- | --- |
| Clientes distintos que compraram via App em maio de 2025, por estado | São Paulo 6; Minas Gerais 3; Santa Catarina 3; empate na sequência |
| Clientes distintos que interagiram com campanhas de WhatsApp em 2024 | 17 |
| Compras médias por comprador de cada categoria | Roupas 2,21; Viagens 2,16; Livros 1,98; demais categorias no arquivo de avaliação |
| Reclamações não resolvidas por canal | Telefone 19; Chat 18; E-mail 14 |
| Reclamações por canal no ano anterior à última data disponível | 97 no total; série mensal por canal |

Esses números são oráculos de verificação, não respostas embutidas no produto. Uma resposta SQL sintaticamente válida ainda pode interpretar mal uma pergunta; confira período, denominador, tabela e filtros na trilha antes de tomar uma decisão. O protótipo também não comprova automaticamente que cada afirmação do texto gerado coincide com os valores da tabela exibida.

Para reproduzir com o arquivo recebido, abra `python -m sqlite3 "$env:DATABASE_PATH"` no PowerShell (ou `python -m sqlite3 "$DATABASE_PATH"` no Bash) e rode os cinco blocos de `evals/reference_queries.sql` em qualquer cliente SQLite. O banco não está no Git; sem ele, um clone não consegue validar os valores numéricos.

### Testes locais

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Os testes automatizados usam um banco sintético e cliente de modelo simulado; não gastam API. A avaliação com o banco do desafio é opcional e requer o arquivo local. Nenhum dado pessoal é necessário para os testes unitários.

Em 22/10/2026, uma verificação ponta a ponta autorizada com a API e o banco anexo respondeu **17** à pergunta de clientes distintos que interagiram com campanhas de WhatsApp em 2024. O rastreio exibiu `COUNT(DISTINCT cliente_id)` com filtros de canal, interação e período, em concordância com o oráculo SQL independente. A interface também foi verificada em desktop e largura móvel de 390 px com Playwright, sem rolagem horizontal nessa largura.

## Privacidade e limites

- O banco recebido contém dados pessoais e está ignorado no Git; não o publique, nem inclua `.env` ou `secrets.toml` em commits. A lista de colunas bloqueadas é uma proteção **heurística para este banco**, não um classificador geral de dados sensíveis.
- A API recebe a pergunta, o esquema, SQL gerado, erros e resultados retornados pelas consultas. `store=False` desativa o armazenamento recuperável da resposta na API, mas **não significa ausência total de processamento ou retenção** pelo provedor. Use somente bases cujo envio seja autorizado.
- Este é um protótipo local, sem autenticação multiusuário, auditoria imutável, avaliação estatística de qualidade ou garantia de acerto sem revisão humana.
- Próximas extensões: avaliação automatizada de SQL semântico, parametrização de políticas de PII por banco, limites de custo por pergunta, observabilidade, autenticação e consultas com confirmação humana para cenários sensíveis.

## Desafio Técnico 2 — arquitetura pensada, não implementada

Para o processamento dos 50 PDFs, eu separaria ingestão idempotente (hash do arquivo e estado por documento), extração de texto nativo com OCR apenas quando necessário, classificação com limiar de confiança e roteamento para três esquemas JSON estritos (nota fiscal, contrato, manutenção). Validadores determinísticos verificariam CNPJ, datas, soma de itens e campos obrigatórios; casos incertos ou anômalos iriam a revisão humana/fila de erros sem interromper o lote. Persistência guardaria JSON, versão do extrator, evidência de origem e estado para reprocessamento. Em escala, filas e workers com concorrência limitada, cache por hash e métricas de custo/latência por etapa permitiriam otimizar milhões de documentos sem aplicar LLM caro a todos. Essa arquitetura é apenas um esboço para a entrevista, como solicitado no enunciado.
