# Estabilização do Desafio Técnico 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar os defeitos reproduzidos do Desafio 1 e entregar uma demonstração local verificável em desktop e celular.

**Architecture:** Preservar o agente único e o contrato de ferramentas. Reforçar a finalização em `agent.py`, validar e executar SQL no mesmo snapshot de leitura em `database.py`, ajustar apenas a camada de apresentação e o avaliador; não criar outra infraestrutura.

**Tech Stack:** Python 3.12, SQLite, sqlglot 30.24.0, Streamlit >=1.65,<2, pandas, pytest, Ruff e Playwright CLI.

**Spec:** `docs/superpowers/specs/2026-10-22-estabilizacao-case-design.md`

## Roteiro completo e skills para continuar

| Fase | Situação | Tarefas verificáveis | Skills e gates |
| --- | --- | --- | --- |
| 0. Escolha e desenho | Concluída | Ler o enunciado, escolher o Desafio 1, fixar interfaces, riscos e limites de publicação | `using-superpowers`, `task-observer`, `pdf`, `brainstorming`, `escopo-fechado`, `codebase-design` |
| 1. Base do produto | Concluída | Executor SQLite, adaptador OpenAI, agente, visualização, Streamlit, cinco oráculos e README; detalhes no [plano inicial](2026-10-22-assistente-dados.md) | `test-driven-development`, `task-impl`, `prompt-engineering-patterns`, `openai-docs`, `verification-before-completion` |
| 2. Estabilização | Concluída | Contratos E1/D1/S1/U1 e regressões das Tasks 1–4B deste plano | `systematic-debugging`, `receiving-code-review`, `test-driven-development`, `task-impl`, `security-best-practices`, `impeccable`, `webapp-testing`, `playwright-cli` |
| 3. Fechamento | Em andamento | Task 5: testes, avaliação real, revisão de segurança, commit, atualização do GitHub e ZIP | `code-reviewer`, `secret-scanning`, `verification-before-completion`, `finishing-a-development-branch` |

O usuário dispensou Strix especificamente neste case; nenhuma fase depende dele. Para a API, a documentação oficial OpenAI é a fonte autoritativa; Context7 não estava disponível neste host. Nenhuma tarefa restante pede OCR, infraestrutura ou publicação do banco.

## Global Constraints

- O Desafio 1 é o único implementado; Desafio 2 permanece esboço arquitetural no README.
- O banco privado e a chave não entram no Git, em logs ou em relatórios; testes automatizados usam dados sintéticos.
- O código de produção não contém SQL específico das cinco perguntas de referência.
- Preservar SQLite `mode=ro`, `query_only`, autorizador, 200 linhas, 128 KiB de saída e prazo por consulta.
- Nenhuma nova dependência é necessária; texto livre do modelo não é prova semântica de acerto.
- Declarar Streamlit >=1.65,<2 para suportar `alt` nos gráficos nativos, conforme notas de versão oficiais.
- Strix está fora desta rodada por decisão expressa do usuário. Usar revisão determinística, testes e Playwright.
- No host Windows restrito, rodar pytest com `--basetemp ..\\..\\work\\pytest-final-20261022-a` ou outro diretório novo dentro de `work/`, a partir da raiz do repositório.

## Mapa de arquivos

- `src/analista_dados/agent.py`: contrato da resposta e verificação conservadora de zero.
- `src/analista_dados/prompts/system_v1.md`: orientações de evidência e semântica de `resolvido`.
- `src/analista_dados/database.py`: inspeção e execução no mesmo snapshot SQLite.
- `evals/live_eval.py`: reprovação da ampliação indevida do estado `NULL` na pergunta 4.
- `src/analista_dados/visualization.py`: eixo temporal legível sem alterar a tabela.
- `app.py` e `.streamlit/config.toml`: sidebar, gráficos, títulos, descrição e telemetria.
- `pyproject.toml`: compatibilidade mínima do Streamlit com `alt` em gráficos.
- `tests/test_agent.py`, `tests/test_database.py`, `tests/test_live_eval.py`, `tests/test_visualization.py`, `tests/test_app.py`: regressões offline.
- `README.md`: execução, limitações, avaliação e ligação para a spec atual.

---

### Task 1: Vincular resposta à consulta e controlar resultados zero

**Files:** Modify `src/analista_dados/agent.py`, `src/analista_dados/prompts/system_v1.md`; Test `tests/test_agent.py`.

**Interfaces:** `DataAnalyst.ask(question) -> Answer` permanece. `_finish(arguments, successful_queries, successful_sql, steps) -> Answer` só seleciona a última consulta bem-sucedida; `query_number=0` retorna esclarecimento neutro. `_has_related_verification(...) -> bool` exige sondagem não redutora da dimensão filtrada, inclusive sem `JOIN`.

- [x] **Step 1: Escrever testes RED para os três contraexemplos.** O teste deve fazer a resposta sem evidência descartar também pressupostos/gráfico; a consulta antiga deve ser recusada; distribuição por `canal` não confirma zero filtrado por `data_evento`.

```python
model = ScriptedModel(finish(answer="Há 999 eventos. Qual período?", query_number=0))
model.calls[0][1]["assumptions"] = ["Há 999 eventos"]
answer = DataAnalyst(store, model).ask("Como evoluiu?")
assert "999" not in answer.text + answer.assumptions
assert answer.data is None and answer.chart.kind == "table"

# Após SELECT COUNT(*) = 2 e SELECT COUNT(*) com canal='App' = 1:
# finish(query_number=1) deve levantar AgentError, nunca exibir a consulta antiga.
# Após GROUP BY canal e COUNT(*) WHERE date(data_evento)>='2030-01-01':
# duas tentativas de finish sobre zero devem levantar AgentError.
```

- [x] **Step 2: Executar os testes novos e confirmar falha pelo comportamento antigo.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_agent.py -q --basetemp ..\\..\\work\\pytest-agent-red --tb=short`. Expected: falhas de asserção nos novos testes; os controles existentes continuam válidos.
- [x] **Step 3: Implementar a menor correção.** No ramo `query_number=0`, retornar `Answer` com texto fixo, pressupostos vazios, `data=None` e `ChartSpec()`; para índice positivo exigir `query_number == len(successful_queries)`. Em `_has_related_verification`, coletar colunas do `WHERE` final, rejeitar sondagem filtrada/truncada e cobrir cada coluna por distribuição `GROUP BY` **projetada** ou `MIN/MAX` **projetado** da mesma coluna na tabela relacionada. Atualizar o prompt para colocar a agregação de apresentação por último.

```python
if query_number == 0:
    return Answer(
        text="Qual período, métrica ou recorte você quer analisar?",
        assumptions="",
        data=None,
        chart=ChartSpec(),
        steps=tuple(steps),
    )
if query_number != len(successful_queries):
    raise AgentError("A resposta deve usar a última consulta bem-sucedida.", tuple(steps))
```

- [x] **Step 4: Rodar GREEN e controles multi-consulta/zero legítimo.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_agent.py -q --basetemp ..\\..\\work\\pytest-agent-green --tb=short`. Expected: zero falhas, inclusive seleção da segunda consulta e confirmação por distribuição da mesma dimensão. Regressões adicionais: `JOIN ... ON` que reduz a sonda, `GROUP BY` sem projetar a dimensão e `MAX(data)` escondido em `ORDER BY` não confirmam zero.
- [x] **Step 5: Revisar o diff deste pacote.** Verificar que `purpose` continua rotulado como passo operacional, não como resposta factual, e que o texto livre de `query_number=0` não reaparece por outro campo.

### Task 2: Validar e consultar o mesmo snapshot SQLite

**Files:** Modify `src/analista_dados/database.py`; Test `tests/test_database.py`.

**Interfaces:** `SQLiteReadOnly.inspect_schema() -> dict` e `.run_sql(sql) -> QueryResult` permanecem públicas. Criar uma inspeção interna que receba a conexão já aberta; `run_sql` a usa dentro de transação de leitura antes de `validate_sql` e da consulta.

- [x] **Step 1: Escrever testes RED de substituição e WAL.** Aqueça `inspect_schema()` num banco com `clientes.estado='Seguro'`; substitua por outro SQLite de mesmo tamanho/mtime contendo `person@example.invalid` e exija `QueryError` ao agrupar `estado`. Num banco WAL, intercale um `UPDATE` via wrapper de `validate_sql`; a consulta deve manter o valor seguro do snapshot que foi aprovado.

```python
store.inspect_schema()
assert original.stat().st_size == replacement.stat().st_size
os.utime(replacement, ns=(original.stat().st_atime_ns, original.stat().st_mtime_ns))
os.replace(replacement, original)
with pytest.raises(QueryError):
    store.run_sql("SELECT estado, COUNT(*) FROM clientes GROUP BY estado")

# No WAL, monkeypatch database.validate_sql para confirmar a política e
# então confirmar uma escrita concorrente. O resultado ainda é ('Seguro', 1).
```

- [x] **Step 2: Rodar RED.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_database.py -q --basetemp ..\\..\\work\\pytest-db-red --tb=short`. Expected: falhas apenas nos novos cenários ou no teste antigo que exige cache.
- [x] **Step 3: Refatorar a inspeção sem cache de autorização.** Extrair o corpo de leitura do esquema para `_inspect_schema(connection, deadline)`, sem chave por mtime/tamanho. `inspect_schema` abre conexão e delega. `run_sql` abre conexão, inicia `BEGIN`, chama a inspeção nela, executa `validate_sql(statement, schema)`, instala o autorizador e executa o SQL **sem trocar de conexão**. Preservar limites e tratamento de erro.

```python
with closing(self._connect()) as connection:
    connection.execute("BEGIN")
    schema = self._inspect_schema(connection, deadline)
    validate_sql(statement, schema)
    connection.set_authorizer(authorize)
    cursor = connection.execute(statement)
    # A leitura dos resultados continua na mesma conexão/transação.
```

- [x] **Step 4: Atualizar o teste que exigia cache e rodar GREEN.** O teste passa a exigir que a segunda inspeção reflita o arquivo atual, não que pule uma conexão. Run: `.venv\\Scripts\\python.exe -m pytest tests/test_database.py tests/test_sql_policy.py -q --basetemp ..\\..\\work\\pytest-db-green --tb=short`. Expected: zero falhas.
- [x] **Step 5: Revisar limites.** Conferir erros de esquema/timeout, arquivo ausente, WAL, política de categorias e que nenhuma conexão de escrita ou SQL model-generated adicional foi introduzido.

### Task 3: Fixar a interpretação de `resolvido=NULL`

**Files:** Modify `evals/live_eval.py`, `src/analista_dados/prompts/system_v1.md`, `evals/reference_questions.md`; Test `tests/test_live_eval.py`.

**Interfaces:** `assess_case(4, answer, reference, selected_sql, ...) -> CaseAssessment` retorna `FAIL` com `q4_null_expansion` para a ampliação simples comprovada que inclui `resolvido IS NULL` em `COUNT(*)` por canal, ainda que os agregados coincidam neste banco. Formas complexas não comprovadas ficam em `REVIEW`, sem falso `FAIL` por menção sintática a `IS NULL`.

- [x] **Step 1: Escrever teste RED sem banco privado.**

```python
wrong_sql = SQL[3].replace("resolvido = 0", "(resolvido = 0 OR resolvido IS NULL)")
outcome = assess_case(4, _answer(4), REFERENCES[3], wrong_sql)
assert outcome.status == "FAIL"
assert "q4_null_expansion" in outcome.issues
```

- [x] **Step 2: Rodar RED.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_live_eval.py -q --basetemp ..\\..\\work\\pytest-q4-red --tb=short`. Expected: o novo teste vê `REVIEW`, não `FAIL`.
- [x] **Step 3: Implementar a regra estreita e documentar.** Após parsear a árvore SQL no caso 4, reprovar apenas a forma simples `tipo_contato = 'Reclamação' AND (resolvido = 0 OR resolvido IS NULL)` em `suporte`, projetando `COUNT(*)` agrupado por `canal`. Para menções positivas a `IS NULL` em estruturas diferentes, solicitar `REVIEW`; negação de `IS NULL` não é ampliação comprovada. No prompt, estabelecer `resolvido=0` como não resolvido e `NULL` como desconhecido por padrão. Em `reference_questions.md`, registrar a distinção sem embutir SQL de referência no código de produção.

```python
if case == 4 and _q4_null_expansion_verdict(tree) == "wrong":
    failures.append("q4_null_expansion")
```

- [x] **Step 4: Rodar GREEN e o avaliador offline.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_live_eval.py -q --basetemp ..\\..\\work\\pytest-q4-green --tb=short`. Expected: zero falhas; outros `OR` continuam no mínimo `REVIEW`. Regressões adicionais: `NOT (resolvido IS NULL OR resolvido = 1)`, guarda externa `resolvido IS NOT NULL` e `COUNT(resolvido)` são `REVIEW`, não `FAIL`.
- [x] **Step 5: Revisar escopo.** A verificação é específica da pergunta 4; não afirmar que analisa toda equivalência lógica possível de SQL.

### Task 4: Corrigir leitura móvel e gráficos

**Files:** Modify `app.py`, `src/analista_dados/visualization.py`, `.streamlit/config.toml`, `pyproject.toml`; Test `tests/test_app.py`, `tests/test_visualization.py`.

**Interfaces:** `prepare_visualization(...) -> PreparedVisualization` conserva `table` literal; quando o eixo `line` é mês ISO, `frame` passa a usar datas de primeiro dia do mês. `render_answer()` mantém controles e tabela, mas usa barra horizontal e rótulos acessíveis.

- [x] **Step 1: Escrever testes RED de comportamento.** Um eixo mensal `2024-07`, `2025-07` deve virar duas datas distintas no `frame` sem mudar `table`; `render_answer()` de comparação deve chamar `st.bar_chart` com `horizontal=True`. Manter testes existentes de override e fallback.

```python
view = prepare_visualization(
    _result(("mes", "total"), (("2024-07", 1), ("2025-07", 2))),
    ChartSpec("line", "mes", "total", ""),
)
assert list(view.frame["mes"].dt.strftime("%Y-%m")) == ["2024-07", "2025-07"]
assert list(view.table["mes"]) == ["2024-07", "2025-07"]
```

- [x] **Step 2: Rodar RED.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_visualization.py tests/test_app.py -q --basetemp ..\\..\\work\\pytest-ui-red --tb=short`. Expected: apenas os novos testes falham.
- [x] **Step 3: Implementar UI focal.** Trocar `initial_sidebar_state` por `auto`; usar barra horizontal com altura proporcional às linhas e descrição de acessibilidade, como no código abaixo; a linha recebe `x_label="Mês (ano)"` e descrição. Converter somente meses ISO no `frame` de linha, não na tabela; trocar subtítulos `###` por `##`. Configurar `[browser] gatherUsageStats = false` e `streamlit>=1.65,<2` no `pyproject.toml`.

```python
if view.kind == "bar":
    st.bar_chart(
        view.frame,
        x=view.x_column,
        y=list(view.y_columns),
        horizontal=True,
        height=max(300, min(720, 36 * len(view.frame))),
        width="stretch",
        alt="Comparação dos valores por categoria",
    )
```

- [x] **Step 4: Rodar GREEN.** Run: `.venv\\Scripts\\python.exe -m pytest tests/test_visualization.py tests/test_app.py -q --basetemp ..\\..\\work\\pytest-ui-green --tb=short`. Expected: zero falhas.
- [x] **Step 5: Validar em Playwright CLI.** Em contexto novo 390×844 e desktop 1280×800, conferir sidebar, visibilidade do título/pergunta, Q1 com nomes completos, Q5 com período 07/2024–07/2025 explícito junto ao gráfico, tabela/SQL e alternância manual. Registrar captura apenas de agregados; se o eixo temporal continuar ambíguo, ajustar o renderizador e repetir uma rodada bounded.

### Task 4A: Confirmar zero em flags sem expor linhas

**Files:** Modify `src/analista_dados/database.py`, `src/analista_dados/sql_policy.py`, `src/analista_dados/prompts/system_v1.md`; Test `tests/test_database.py`, `tests/test_sql_policy.py`, `tests/test_agent.py`.

**Interfaces:** O schema pode marcar `safe_flag_columns` somente para flag de nome aprovado, tipo declarado `INTEGER` ou `BOOLEAN` exato e domínio efetivo inteiro `0`/`1`/`NULL` no snapshot. A política permite distribuição agregada de flag aprovada; tipo `NUMERIC` genérico, valor fora do domínio ou projeção por linha continuam bloqueados. O agente usa a dimensão projetada como sonda antes de concluir zero.

- [x] **Step 1: Reproduzir RED.** Para `resolvido`/`interagiu` sintéticos com valor 0 ausente, `SELECT flag, COUNT(*) ... GROUP BY flag` é recusado e o agente não consegue concluir zero verdadeiro. Guardar a falha em testes.
- [x] **Step 2: Implementar aprovação fail-closed no snapshot.** Verificar nome, declaração exata e todos os tipos/valores efetivos sem publicar linhas/contagens no schema; validar, agrupar e executar na mesma transação de leitura. `BOOLEAN` do banco anexo é aceito somente como exceção exata, não por afinidade `NUMERIC`. Não usar `COUNT(flag)` como substituto de distribuição.
- [x] **Step 3: Provar privacidade e GREEN.** Testar domínio 0/1/NULL, texto armazenado em coluna INTEGER, valor extra, projeção crua, ordenação permitida e verificação de zero do agente; rodar testes de política, banco e agente. Resultado intermediário: 187 testes direcionados e os cinco SQLs de referência aprovados; hash do banco anexo inalterado.

### Task 4B: Evitar falsos veredictos por texto na tendência

**Files:** Modify `evals/live_eval.py`; Test `tests/test_live_eval.py`.

**Interfaces:** O avaliador não confunde contagem por canal com total da pergunta 5 e não aprova automaticamente um número por canal divergente apenas porque a série SQL está correta. Predicados essenciais negados nas perguntas 1, 2 e 4 não recebem `PASS` por mera coincidência de agregados.

- [x] **Step 1: Reproduzir RED sintético.** Texto correto com Chat 5, E-mail 7, Telefone 9 não pode ser `FAIL`; texto com Telefone 999 sobre série correta não pode ser `PASS`. A revisão posterior ainda reproduziu falso `PASS` para `999,5` e `Chat: 5,9`, falso `FAIL` para `Em julho de 2024, Chat teve 1 reclamação`, recortes subanuais e soma de canais, além de `PASS` indevido para `NOT` de filtro essencial e `canal <> 'App'` com agregados coincidentes.
- [x] **Step 2: Completar leitura conservadora de afirmações e predicados.** Comparar apenas relações texto↔canal reconhecíveis e tratar frase ambígua, contagem fracionária, quantidade não interpretada, recorte subanual ou soma de canais como `REVIEW`. Negação de filtro essencial e comparação de canal incompatível também requerem revisão, sem usar o texto como substituto do SQL e da série.
- [x] **Step 3: Rodar GREEN e regressões das cinco perguntas.** Confirmar casos canônicos, números errados, decimais, frases temporais/ambíguas, SQL negado e ausência de vazamento no log. Resultado: 96 testes do avaliador e 337 testes da suíte integral aprovados; Ruff check/format e diff --check aprovados.

### Task 5: Documentação, gates e publicação

**Files:** Modify `README.md`; review all files above and the spec; update user-facing review result outside the repository only se necessário.

**Interfaces:** Os comandos README permanecem válidos. O repositório público e o pacote local refletem a mesma versão validada; dados/chave permanecem externos.

- [x] **Step 1: Atualizar README.** Substituir a alegação de cache por validação no mesmo snapshot; descrever `category_fields` restritivo, regras de resposta/zero e limite semântico. Ligar spec/plano atuais e preservar as seções exigidas pelo enunciado.
- [x] **Step 2: Rodar gates offline na árvore final.** Run: `.venv\\Scripts\\python.exe -m pytest -q --basetemp ..\\..\\work\\pytest-final --tb=short`, `.venv\\Scripts\\python.exe -m ruff check .` e `.venv\\Scripts\\python.exe -m ruff format --check .`. Resultado em 22/10/2026: 337 testes aprovados, Ruff check/format, `pip check` e diff --check com exit code 0.
- [x] **Step 3: Rodar avaliação real autorizada, se configuração presente.** Run: `.venv\\Scripts\\python.exe -m evals.live_eval` com `DATABASE_PATH` local, sem imprimir chave. A última rodada integral teve cinco agregados coincidentes, 5 `REVIEW` por texto e nenhum `FAIL`; o exit code 1 é intencional para revisão pendente. Houve um `FAIL` de agregado na pergunta 2 em rodada intermediária, seguido de correção geral do prompt e resultado coincidente em teste dirigido e na última rodada integral. Não confundir variabilidade do modelo com aprovação estável.
- [x] **Step 4: Revisar diff e segredos.** Diff e testes revisados por duas passagens de código, varredura de padrões de segredo/caminho e inventário de arquivos rastreados; nenhum banco/chave encontrado. `git diff --cached --check` passou. `code-review-graph` não funcionou neste host (`uv trampoline failed to canonicalize script path`); revisão manual. Strix não executado, por decisão do usuário.
- [x] **Step 5: Publicar só após os gates.** Fazer commit coeso, atualizar o repositório público já autorizado sem incluir banco/chave, gerar ZIP local sem `.venv`/artefatos temporários e conferir hash/árvore. Registrar no fechamento os gates executados, ressalvas de avaliação e limitações não testadas.

## Auto-revisão do plano

- E1 está no Task 1; D1 no Task 2; S1 no Task 3; U1 no Task 4; entregáveis e gates no Task 5.
- `system_v1.md` é compartilhado por Tasks 1 e 3: Task 1 altera apenas o contrato da consulta final; Task 3 altera apenas a definição de `resolvido`.
- `QueryResult`, `Answer` e `PreparedVisualization` mantêm suas interfaces públicas; nenhum Task depende de função ainda não criada por outro.
- O alvo de toque nativo e a portabilidade de nomes categóricos continuam limites documentados, não falhas escondidas.
