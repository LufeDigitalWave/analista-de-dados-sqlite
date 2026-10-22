# Assistente de Dados SQLite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o Desafio Técnico 1 como aplicação local que responde perguntas sobre SQLite com SQL dinâmico, gráfico/tabela e rastreio das consultas.

**Architecture:** Um agente chama ferramentas estritas de descoberta de schema e SQL. O executor SQLite é o limite de segurança; a interface apenas apresenta o objeto `Answer`. O provedor OpenAI é injetável para testes sem rede.

**Tech Stack:** Python 3.12, SQLite, OpenAI Responses API (`gpt-4.1`), Streamlit, pandas, pytest e Ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-assistente-dados-design.md`

## Global Constraints

- Banco e `.env` não entram no Git; `.gitignore` existe antes de `git init`.
- Executar apenas `SELECT`/`WITH` em conexão SQLite `mode=ro`, com autorizador, até 200 linhas e prazo finito.
- Usar `store=False`, no máximo uma tool call por resposta e modelo configurável com padrão `gpt-4.1`.
- Exibir objetivo operacional, SQL, erro e linhas de cada passo, sem raciocínio interno do modelo.
- O produto não contém SQL específico das perguntas do enunciado.
- Testes offline usam banco sintético e modelo simulado; chamadas reais são opcionais e limitadas.

## Mapa de arquivos

- `src/analista_dados/database.py`: schema dinâmico e execução SQLite segura.
- `src/analista_dados/models.py`: tipos de resultado, chamada de ferramenta, trilha e resposta.
- `src/analista_dados/model.py`: tradução Responses API para `ModelTurn`.
- `src/analista_dados/agent.py`: loop finito de ferramentas e autocorreção.
- `src/analista_dados/visualization.py`: validação da sugestão e preparo da tabela/gráfico.
- `src/analista_dados/prompts/system_v1.md`: comportamento versionado do agente.
- `app.py`: interface Streamlit sem regras de negócio.
- `tests/`: contratos de banco, modelo, agente e gráfico com fixtures sintéticas.
- `README.md`, `.env.example`, `pyproject.toml`: execução e entrega.

---

### Task 1: Banco e contrato de dados

**Files:** Create `pyproject.toml`, `src/analista_dados/__init__.py`, `src/analista_dados/models.py`, `src/analista_dados/database.py`, `tests/test_database.py`.

**Interfaces:** `SQLiteReadOnly(path: Path, row_limit: int = 200, timeout_seconds: float = 3.0)`, `inspect_schema() -> dict`, `run_sql(sql: str) -> QueryResult`. `QueryResult` contém `columns`, `rows`, `truncated` e `elapsed_ms`. SQL rejeitado ou inválido levanta `QueryError` com mensagem controlada.

- [ ] **Step 1: Escrever teste RED com banco temporário.**

```python
def test_query_is_read_only_and_schema_is_discovered(tmp_path):
    db = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    store = SQLiteReadOnly(db)
    assert store.inspect_schema()["tables"][0]["name"] == "eventos"
    assert store.run_sql("SELECT COUNT(*) AS total FROM eventos").rows == ((0,),)
    with pytest.raises(QueryError):
        store.run_sql("DELETE FROM eventos")
```

- [ ] **Step 2: Executar** `python -m pytest tests/test_database.py -q`; confirmar falha por ausência da implementação.
- [ ] **Step 3: Implementar** abertura `file:...?...mode=ro`, schema via `sqlite_master`/`PRAGMA table_info`/`foreign_key_list`, autorizador para leitura, `fetchmany(row_limit + 1)` e progress handler por prazo. Bloquear leitura direta de colunas identificadoras (`nome`, `email`, `cpf`, `telefone`) sem impedir `COUNT(DISTINCT id)` e joins.
- [ ] **Step 4: Acrescentar contraprovas RED/GREEN** para `WITH`, múltiplas instruções, `PRAGMA`, escrita, schema de tabela nova, erro SQL, limite de linhas e consulta recursiva longa.
- [ ] **Step 5: Executar** `python -m pytest tests/test_database.py -q` e registrar resultado; commit coeso.

### Task 2: Adaptador OpenAI e prompt

**Files:** Create `src/analista_dados/model.py`, `src/analista_dados/prompts/system_v1.md`, `tests/test_model.py`.

**Interfaces:** `OpenAIModel(client, model: str = "gpt-4.1").next_turn(history: list) -> ModelTurn`. `ModelTurn` contém um `ToolCall(name, arguments, call_id)` e `output_items` para continuar o histórico. Ferramentas estritas: `inspect_schema`, `run_sql(sql, purpose)` e `finish_answer(answer, query_number, visualization, x_column, y_column, series_column, assumptions)`.

- [ ] **Step 1: Escrever teste RED** com cliente falso que registra a chamada e devolve uma única `function_call` para `inspect_schema`; verificar campos transformados e parâmetros `store=False`, `parallel_tool_calls=False`, `tool_choice="required"`.

```python
turn = OpenAIModel(fake_client).next_turn([{"role": "user", "content": "Quantos?"}])
assert turn.call.name == "inspect_schema"
assert fake_client.kwargs["store"] is False
```

- [ ] **Step 2: Executar** `python -m pytest tests/test_model.py -q` e confirmar RED.
- [ ] **Step 3: Implementar** o adaptador seguindo a documentação oficial de function calling; rejeitar status incompleto, recusa, zero ou múltiplas chamadas e argumentos malformados. O prompt exige consultas de evidência antes de números, linguagem portuguesa, interpretação explícita de períodos e agregação por cliente distinto quando solicitado.
- [ ] **Step 4: Exercitar** respostas incompletas, 429/erro de conexão simulado e tool call inesperada; verificar erro controlado, sem chave em logs.
- [ ] **Step 5: Executar** `python -m pytest tests/test_model.py -q` e registrar resultado; commit coeso.

### Task 3: Loop do agente

**Files:** Create `src/analista_dados/agent.py`, `tests/test_agent.py`.

**Interfaces:** `DataAnalyst(db: SQLiteReadOnly, model: Model, max_turns: int = 8, max_sql_errors: int = 2).ask(question: str) -> Answer`. `Answer` possui texto, pressupostos, dados selecionados, sugestão de gráfico e trilha.

- [ ] **Step 1: Escrever teste RED** com modelo simulado que retorna `inspect_schema`, SQL inválido, SQL corrigido e `finish_answer`; conferir resposta factual e que a trilha contém erro e correção.

```python
answer = DataAnalyst(db, scripted_model).ask("Quantos eventos existem?")
assert answer.text == "Existem 2 eventos."
assert len(answer.steps) == 3
assert answer.data.rows == ((2,),)
```

- [ ] **Step 2: Executar** `python -m pytest tests/test_agent.py -q` e confirmar RED.
- [ ] **Step 3: Implementar** roteamento de ferramentas, histórico com `response.output` e `function_call_output`, checagem de número de consulta para `finish_answer` e limites de passos/falhas. Devolver erro útil sem travar a interface quando o limite for atingido.
- [ ] **Step 4: Exercitar** duas consultas necessárias, pergunta vazia, resposta sem evidência, fim prematuro e repetição infinita simulada.
- [ ] **Step 5: Executar** `python -m pytest tests/test_agent.py -q` e registrar resultado; commit coeso.

### Task 4: Visualização e Streamlit

**Files:** Create `src/analista_dados/visualization.py`, `app.py`, `.streamlit/config.toml`, `tests/test_visualization.py`.

**Interfaces:** `prepare_visualization(result: QueryResult, suggestion: ChartSpec, override: str = "auto") -> PreparedVisualization`. O gráfico usa somente colunas presentes e numéricas; tabela sempre acompanha o resultado. `app.py` chama apenas `DataAnalyst.ask` e a função de apresentação.

- [ ] **Step 1: Escrever teste RED** para comparação por canal em barras e tendência mensal com série por canal; sugestão inválida cai para tabela.

```python
view = prepare_visualization(result, ChartSpec("bar", "canal", "total", ""))
assert view.kind == "bar"
assert list(view.frame.columns) == ["canal", "total"]
```

- [ ] **Step 2: Executar** `python -m pytest tests/test_visualization.py -q` e confirmar RED.
- [ ] **Step 3: Implementar** preparo de gráfico e interface: pergunta por `st.chat_input`, exemplos clicáveis, status de configuração, resposta, pressupostos, gráfico/tabela, seleção manual do visual e expander com SQL/erros. Estilo Operate: contraste alto, poucos controles e largura responsiva.
- [ ] **Step 4: Executar** testes de visualização e abrir o app; validar no navegador em desktop e celular com Playwright, incluindo envio de pergunta e exibição do SQL.
- [ ] **Step 5: Registrar** resultado e commit coeso.

### Task 5: Documentação, oráculos e gates

**Files:** Create `README.md`, `.env.example`, `evals/reference_questions.md`; update docs de arquitetura se a implementação divergir.

**Interfaces:** README informa instalação, execução, fluxo, exemplos, limites, privacidade e arquitetura conceitual do Desafio 2. `evals/reference_questions.md` contém as cinco perguntas e métricas agregadas verificadas no banco fornecido.

- [ ] **Step 1: Documentar** instalação em ambiente virtual, cópia local do banco, chave via ambiente e comando `streamlit run app.py`; incluir links para a documentação oficial consultada.
- [ ] **Step 2: Executar** `python -m pytest -q`, `ruff check .`, smoke Streamlit/Playwright e ao menos uma pergunta real se a API estiver disponível; registrar resultados reais e custos/limites.
- [ ] **Step 3: Fazer revisão de segurança** dos arquivos versionados e do diff; conferir exclusão do banco/chaves, SQL somente leitura e payloads da API.
- [ ] **Step 4: Executar gate Strix** rápido em snapshot isolado se Docker e Strix estiverem disponíveis, com orçamento do AGENTS.md; relatar cobertura e custo.
- [ ] **Step 5: Revisar** spec, plano e README contra o enunciado, conferir `git status`, produzir ZIP ou pasta local pronta para GitHub e relatório final com pendências de publicação.
