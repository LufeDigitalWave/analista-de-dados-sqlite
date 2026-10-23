# Assistente de dados SQLite

Implementação do **Desafio Técnico 1**: um assistente que recebe perguntas em português, descobre o esquema do banco em tempo de execução, consulta SQLite e apresenta resposta, dados, gráfico quando pertinente e as consultas executadas. Não há SQL pronto para as perguntas do enunciado no código da aplicação.

A [especificação de estabilização](docs/superpowers/specs/2026-10-22-estabilizacao-case-design.md) e o [plano de tarefas](docs/superpowers/plans/2026-10-22-estabilizacao-case.md) registram escopo, critérios de aceite, riscos e verificações desta entrega.
O [fechamento verificável](docs/superpowers/specs/2026-10-22-fechamento-evidencias-design.md) e seu [plano de execução](docs/superpowers/plans/2026-10-22-fechamento-evidencias.md) documentam a revisão manual das cinco perguntas e os limites da evidência.
O [estado consolidado da entrega](docs/STATUS-ENTREGA.md) separa evidência verificada, revisão pendente e limites do protótipo.

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
            ├─ inspect_schema → esquema e contagens de categorias seguras
            ├─ run_sql        → executor somente leitura → resultado ou erro
            └─ finish_answer → resposta + índice da consulta de evidência
         → Streamlit: texto + tabela/gráfico + trilha de SQL
```

Há **um agente e três ferramentas**, sem LangChain/LangGraph: para este escopo, o [function calling nativo](https://developers.openai.com/api/docs/guides/function-calling) mantém o fluxo curto e testável. O modelo não recebe o esquema fixo no prompt; deve descobri-lo em cada pergunta. O catálogo informa as grafias realmente presentes apenas em dimensões categóricas revisadas, com contagens, para evitar filtros que confundam maiúsculas ou acentos. Se uma consulta falhar, recebe o erro controlado e pode tentar corrigi-la. Pode executar consultas intermediárias antes de concluir; a consulta de apresentação precisa ser a **última consulta bem-sucedida**. Uma resposta baseada em resultado vazio ou zero exige sondagem anterior, sem filtro redutor nem `JOIN`, da dimensão usada no recorte. Sem evidência SQL, o servidor exibe apenas um pedido neutro de esclarecimento, sem repetir afirmações livres do modelo. O aplicativo não exibe pensamento privado do modelo, apenas passos operacionais.

O executor abre o arquivo em `mode=ro`, ativa `query_only`, valida a árvore SQL antes de executar uma única instrução `SELECT`/`WITH`, usa autorizador SQLite, bloqueia acesso direto a colunas pessoais conhecidas e interrompe consultas demoradas. Por padrão: 200 linhas e 128 KiB de resultado por consulta, 3 segundos por SQL, 8 rodadas do agente, até 4 erros SQL, 4.000 caracteres por pergunta e limite SQLite de 64 KiB para valores/texto. A inspeção do esquema também tem limite de tempo/tamanho. Na execução de cada SQL, a inspeção de autorização, a validação e a leitura ocorrem na mesma conexão e transação SQLite, sem reutilizar uma aprovação de esquema em cache. As flags `resolvido` e `interagiu` só recebem aprovação se o tipo declarado e todos os valores reais no snapshot forem compatíveis com `BOOLEAN`/inteiro `0`/`1`/`NULL`; podem então ser filtradas ou agrupadas com uma métrica, mas não exibidas registro a registro. A camada de visualização valida as colunas e o eixo numérico antes de desenhar o gráfico; a adequação semântica de barras ou linha ainda depende do agente e da revisão humana. A tabela continua disponível. Se o resultado estiver truncado, a interface avisa e não o transforma em gráfico. Texto da pergunta e do modelo é exibido literalmente, sem interpretar Markdown ou carregar imagens remotas.

Em telas estreitas, a barra lateral inicia recolhida; comparações usam barras horizontais. Nas séries mensais, a tabela mantém os rótulos `AAAA-MM` e o gráfico indica o primeiro e o último mês/ano por escrito, pois os ticks nativos podem abreviar dois anos diferentes com o mesmo nome de mês.

## Exemplos e avaliação

Perguntas verificadas contra o banco fornecido, com definições em [evals/reference_questions.md](evals/reference_questions.md) e SQL independente em [evals/reference_queries.sql](evals/reference_queries.sql):

| Pergunta | Referência agregada |
| --- | --- |
| Clientes distintos que compraram via App em maio de 2025, por estado | São Paulo 6; Minas Gerais 3; Santa Catarina 3; empate na sequência |
| Clientes distintos que interagiram com campanhas de WhatsApp em 2024 | 17 |
| Compras médias por comprador de cada categoria | Roupas 2,21; Viagens 2,16; Livros 1,98; demais categorias no arquivo de avaliação |
| Reclamações não resolvidas por canal | Telefone 19; Chat 18; E-mail 14 |
| Reclamações por canal no ano anterior à última data disponível | 97 no total; série mensal por canal |

Esses números são oráculos de verificação, não respostas embutidas no produto. Uma resposta SQL sintaticamente válida ainda pode interpretar mal uma pergunta; confira período, denominador, tabela e filtros na trilha antes de tomar uma decisão. Mesmo uma sondagem válida de zero confirma somente a dimensão observada, não que o filtro final corresponde à pergunta: um predicado extra ou contraditório pode produzir zero indevido. Cada SQL usa seu próprio snapshot de leitura; uma escrita externa entre sonda e consulta final pode invalidar a confirmação do zero. O avaliador automático compara os agregados, alguns filtros essenciais, o gráfico temporal e afirmações numéricas reconhecíveis no texto. `REVIEW` significa que os dados podem estar corretos, mas a forma do SQL ou da resposta não foi comprovada automaticamente; o verificador não é uma prova geral de equivalência semântica.

Para reproduzir com o arquivo recebido, abra `python -m sqlite3 "$env:DATABASE_PATH"` no PowerShell (ou `python -m sqlite3 "$DATABASE_PATH"` no Bash) e rode os cinco blocos de `evals/reference_queries.sql` em qualquer cliente SQLite. O banco não está no Git; sem ele, um clone não consegue validar os valores numéricos.

### Testes locais

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Os testes automatizados usam um banco sintético e cliente de modelo simulado; não gastam API. A avaliação com o banco do desafio é opcional e requer o arquivo local. Nenhum dado pessoal é necessário para os testes unitários.

Para repetir as cinco perguntas com a API (chamadas pagas, explicitamente separadas dos testes locais):

```powershell
$env:DATABASE_PATH = 'C:\caminho\para\anexo_desafio_1.db'
python -m evals.live_eval
```

O comando imprime apenas estado, contagens agregadas e identificadores hash das consultas; não imprime SQL, linhas privadas nem a chave. Encerra com código 0 somente quando os cinco casos passam.

Para uma **nova rodada paga** com evidência local de revisão, use `python -m evals.live_eval --private-review` com as mesmas variáveis de ambiente. Essa opção exige um commit local e árvore Git limpa antes e depois, registra hash do banco e cria um JSON novo em `work/private-review/` fora de `outputs/` neste workspace; em clone comum, exige que o Git ignore o destino. O arquivo pode conter texto, SQL e resultados agregados sensíveis. **Não o inclua no Git, ZIP ou e-mail.** O comando normal, sem a opção, não cria esse pacote. Os veredictos automáticos não substituem conferir pergunta, SQL, tabela e redação.

Em uma execução integral de 22/10/2026 com o banco anexo e `gpt-4.1`, os agregados das cinco respostas coincidiram com os oráculos independentes. O gate conservador marcou **5 REVIEW**, todos por afirmações no texto não comprovadas automaticamente, sem `FAIL` nessa execução. A quinta série teve 38 linhas e totalizou as 97 reclamações do período. `REVIEW` não é aprovação automática nem erro comprovado: confira a trilha SQL e a tabela. A trajetória do modelo varia: uma execução anterior nesta revisão teve divergência comprovada na pergunta 2 (`FAIL`) e formato de tabela não reconhecido na 3 (`REVIEW`). Após reforço geral do prompt, a pergunta 2 passou em teste dirigido e voltou a coincidir na rodada integral. Portanto, não há garantia de acerto estável em toda tentativa. O teste de navegador após estas correções é registrado separadamente no relatório da entrega.

**Estado mais recente (23/10/2026), que prevalece sobre o parágrafo acima.** A rodada integral mais recente com `gpt-4.1` teve Q1/Q3/Q4 em `REVIEW`, Q5 em `PASS` e **Q2 em `FAIL`**: o modelo substituiu o ano civil pedido ("em 2024") por uma janela móvel de 12 meses, e SQL e resposta saíram incorretos. Em reação, o prompt agora afirma que um ano civil nomeado prevalece sobre regras de período relativo, com teste de regressão offline (que prova apenas o texto do prompt, não a obediência do modelo). A repetição da Q2 com `gpt-4.1` não foi possível: a API respondeu `429 credit_balance_exhausted` antes de qualquer consulta, e essa tentativa não conta como teste do reparo. Duas amostras independentes da Q2 com outro modelo (Gemini `gemini-3-flash-preview`, fora do produto) tiveram o agregado coincidindo com o oráculo (17 clientes): a primeira com veredicto `REVIEW` e a segunda com `PASS`, esta com filtro de 1º/jan a 31/dez de 2024. Isso sugere que a regra funciona, mas duas amostras **não demonstram** o comportamento do `gpt-4.1`; a Q2 segue sem validação estável com o modelo do produto e todas as respostas precisam de conferência humana.

**Conferência pela interface (23/10/2026, assistida, com Gemini).** As cinco perguntas foram feitas no Streamlit, conduzidas pelo `playwright-cli`, com `gemini-3.1-flash-lite` no lugar do `gpt-4.1` (a cota gratuita do `gemini-3-flash-preview` esgotou no meio do teste); o rótulo "Modelo" da barra lateral continuou mostrando `gpt-4.1`, pois vem da configuração. Q1 a Q4 coincidiram com os oráculos na primeira tentativa, e a trilha SQL mostrou os filtros esperados (canal, período, entidade contada, denominador por categoria e `resolvido = 0`). A Q5 **falhou na primeira tentativa**: o modelo usou `GROUP BY 1, 2`, a política recusou o agrupamento três vezes e o limite de tentativas inválidas foi atingido. A mensagem de recusa não orienta o modelo a agrupar por alias ou expressão, o que é um ponto fraco a melhorar. Na segunda tentativa a Q5 concluiu com 38 linhas, período de 07/2024 a 07/2025 e gráfico. Não houve erros no console do navegador. Não foi conferido o total de 97 reclamações nem viewport móvel nesta rodada. Isso não substitui a revisão humana nem valida o comportamento do `gpt-4.1`.

## Privacidade e limites

- O banco recebido contém dados pessoais e está ignorado no Git; não o publique, nem inclua `.env` ou `secrets.toml` em commits. A prévia de valores categóricos só é habilitada por padrão para `clientes.estado`, `compras.canal`, `compras.categoria`, `campanhas_marketing.canal`, `suporte.canal` e `suporte.tipo_contato`. Em outro banco, um operador deve revisar os campos e a política SQL antes de habilitar pares adicionais com `SQLiteReadOnly(..., category_fields={...})`. A lista interna de nomes categóricos permitidos continua restritiva: passar um novo nome em `category_fields` **não** o autoriza por si só a ser projetado ou filtrado. Sem política revisada, a consulta pode ser recusada. Mesmo nos pares aprovados, chaves primárias, identificadores, rótulos com aparência de contato, valores longos e categorias demais não são publicados.
- As regras de colunas, rótulos e SQL são **heurísticas para este banco**, não um classificador geral de PII nem garantia de anonimato: grupos pequenos podem permitir inferências. Não use o protótipo com outro banco sensível sem revisão de esquema, valores permitidos e política de acesso.
- A API recebe a pergunta, o esquema, SQL gerado, erros e resultados retornados pelas consultas. `store=False` desativa o armazenamento recuperável da resposta na API, mas **não significa ausência total de processamento ou retenção** pelo provedor. Use somente bases cujo envio seja autorizado.
- Este é um protótipo local, sem autenticação multiusuário, auditoria imutável, avaliação estatística de qualidade ou garantia de acerto sem revisão humana. O modelo pode gerar SQL recusado ou terminar sem resposta dentro dos limites; repetir a pergunta pode produzir uma trajetória diferente.
- Próximas extensões: política de PII configurável e revisada por banco, limites de custo por pergunta, observabilidade, autenticação e confirmação humana para cenários sensíveis.

## Desafio Técnico 2 — arquitetura pensada, não implementada

Para o processamento dos 50 PDFs, eu separaria ingestão idempotente (hash do arquivo e estado por documento), extração de texto nativo com OCR apenas quando necessário, classificação com limiar de confiança e roteamento para três esquemas JSON estritos (nota fiscal, contrato, manutenção). Validadores determinísticos verificariam CNPJ, datas, soma de itens e campos obrigatórios; casos incertos ou anômalos iriam a revisão humana/fila de erros sem interromper o lote. Persistência guardaria JSON, versão do extrator, evidência de origem e estado para reprocessamento. Em escala, filas e workers com concorrência limitada, cache por hash e métricas de custo/latência por etapa permitiriam otimizar milhões de documentos sem aplicar LLM caro a todos. Essa arquitetura é apenas um esboço para a entrevista, como solicitado no enunciado.
