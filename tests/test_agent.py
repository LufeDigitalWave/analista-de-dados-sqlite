"""Offline contracts for the bounded tool-calling loop."""

import sqlite3

import pytest

from analista_dados.agent import AgentError, DataAnalyst
from analista_dados.database import SQLiteReadOnly
from analista_dados.models import ModelTurn, ToolCall


class ScriptedModel:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.histories = []

    def next_turn(self, history):
        self.histories.append(list(history))
        if not self.calls:
            raise AssertionError("O agente pediu mais uma resposta ao modelo.")
        name, arguments = self.calls.pop(0)
        call_id = f"call-{len(self.histories)}"
        output = {
            "type": "function_call",
            "name": name,
            "arguments": arguments,
            "call_id": call_id,
        }
        return ModelTurn(
            call=ToolCall(name=name, arguments=arguments, call_id=call_id),
            output_items=[output],
        )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "eventos.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE eventos (id INTEGER PRIMARY KEY, canal TEXT);"
            "INSERT INTO eventos(canal) VALUES ('App'), ('WhatsApp');"
        )
    return SQLiteReadOnly(path, category_fields={("eventos", "canal")})


def finish(*, answer="Existem 2 eventos.", query_number=1, chart="table"):
    return (
        "finish_answer",
        {
            "answer": answer,
            "query_number": query_number,
            "visualization": chart,
            "x_column": "",
            "y_column": "",
            "series_column": "",
            "assumptions": [],
        },
    )


def test_recovers_from_invalid_sql_and_returns_evidence(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELEC COUNT(*) FROM eventos", "purpose": "Contar eventos"}),
        (
            "run_sql",
            {"sql": "SELECT COUNT(*) AS total FROM eventos", "purpose": "Corrigir contagem"},
        ),
        finish(),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos existem?")

    assert answer.text == "Existem 2 eventos."
    assert answer.data is not None
    assert answer.data.columns == ("total",)
    assert answer.data.rows == ((2,),)
    assert len(answer.steps) == 3
    assert answer.steps[1].error
    assert answer.steps[2].row_count == 1
    assert any(
        item.get("type") == "function_call_output" and "query_number" in item.get("output", "")
        for item in model.histories[-1]
        if isinstance(item, dict)
    )


def test_default_budget_allows_two_sql_repairs_before_a_valid_answer(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT missing_one FROM eventos", "purpose": "Tentar consulta"}),
        ("run_sql", {"sql": "SELECT missing_two FROM eventos", "purpose": "Revisar consulta"}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Contar eventos"}),
        finish(),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos existem?")
    assert answer.data is not None
    assert answer.data.rows == ((2,),)


def test_can_run_multiple_queries_and_select_second_result(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT COUNT(*) AS total FROM eventos", "purpose": "Total"}),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) AS total FROM eventos GROUP BY canal ORDER BY canal",
                "purpose": "Distribuição",
            },
        ),
        finish(answer="Um evento por canal.", query_number=2, chart="bar"),
    )

    answer = DataAnalyst(store, model).ask("Compare os canais.")

    assert answer.data is not None
    assert answer.data.columns == ("canal", "total")
    assert answer.data.rows == (("App", 1), ("WhatsApp", 1))
    assert len(answer.steps) == 3
    assert answer.chart.kind == "bar"


def test_cannot_select_stale_query_after_a_new_successful_result(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Contar todos"}),
        (
            "run_sql",
            {"sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App'", "purpose": "Filtrar App"},
        ),
        finish(answer="Há dois eventos no App.", query_number=1),
    )

    with pytest.raises(AgentError, match="última consulta"):
        DataAnalyst(store, model).ask("Quantos eventos ocorreram no App?")


def test_retries_after_empty_group_instead_of_claiming_no_data(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": (
                    "SELECT canal, COUNT(*) FROM eventos "
                    "WHERE canal = 'App' AND canal = 'WhatsApp' GROUP BY canal"
                ),
                "purpose": "Contar o filtro inicial",
            },
        ),
        finish(answer="Não há eventos no filtro."),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) FROM eventos WHERE canal = 'App' GROUP BY canal",
                "purpose": "Usar o rótulo exato",
            },
        ),
        finish(answer="Há um evento no App.", query_number=2),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos do App?")

    assert answer.data is not None
    assert answer.data.rows == (("App", 1),)
    assert "um evento" in answer.text


def test_retries_after_zero_scalar_before_claiming_no_data(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar",
            },
        ),
        finish(answer="Nenhum evento."),
        (
            "run_sql",
            {"sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App'", "purpose": "Recontar"},
        ),
        finish(answer="Há um evento.", query_number=2),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos do App?")

    assert answer.data is not None
    assert answer.data.rows == ((1,),)


def test_accepts_confirmed_zero_after_category_distribution(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {"sql": "SELECT canal, COUNT(*) FROM eventos GROUP BY canal", "purpose": "Ver canais"},
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento pertence simultaneamente aos dois canais.", query_number=2),
    )

    answer = DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")

    assert answer.data is not None
    assert answer.data.rows == ((0,),)


def test_filtered_distribution_does_not_verify_zero(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) FROM eventos WHERE canal = 'App' GROUP BY canal",
                "purpose": "Ver distribuição filtrada",
            },
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_distribution_must_project_the_grouped_dimension_to_verify_zero(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos GROUP BY canal",
                "purpose": "Contar grupos sem mostrar os canais",
            },
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_truncated_distribution_does_not_verify_zero(store):
    limited_store = SQLiteReadOnly(store.path, row_limit=1, category_fields={("eventos", "canal")})
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) FROM eventos GROUP BY canal ORDER BY canal",
                "purpose": "Ver distribuição limitada",
            },
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(limited_store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_joined_probe_that_drops_rows_does_not_verify_zero(store):
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "CREATE TABLE marcadores (id INTEGER PRIMARY KEY, evento_id INTEGER);"
            "INSERT INTO marcadores(evento_id) VALUES (1);"
        )
    probe_sql = (
        "SELECT e.canal, COUNT(*) FROM eventos AS e "
        "JOIN marcadores AS m ON m.evento_id = e.id GROUP BY e.canal"
    )
    assert store.run_sql(
        "SELECT canal, COUNT(*) FROM eventos GROUP BY canal ORDER BY canal"
    ).rows == (
        ("App", 1),
        ("WhatsApp", 1),
    )
    assert store.run_sql(probe_sql).rows == (("App", 1),)
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": probe_sql, "purpose": "Ver distribuição após junção"}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_other_dimension_distribution_does_not_verify_zero_on_date(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE eventos ADD COLUMN data_contato TEXT")
        connection.execute("UPDATE eventos SET data_contato = '2024-01-01'")
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {"sql": "SELECT canal, COUNT(*) FROM eventos GROUP BY canal", "purpose": "Ver canais"},
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE date(data_contato) >= '2030-01-01'",
                "purpose": "Contar período",
            },
        ),
        finish(answer="Não houve eventos no período.", query_number=2),
        finish(answer="Não houve eventos no período.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos a partir de 2030?")


def test_date_extreme_verifies_zero_on_the_same_date_column(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE eventos ADD COLUMN data_contato TEXT")
        connection.execute("UPDATE eventos SET data_contato = '2024-01-01'")
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT MAX(date(data_contato)) AS fim FROM eventos",
                "purpose": "Verificar data máxima",
            },
        ),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE date(data_contato) >= '2030-01-01'",
                "purpose": "Contar período",
            },
        ),
        finish(answer="Nenhum evento a partir de 2030.", query_number=2),
    )

    answer = DataAnalyst(store, model).ask("Há eventos a partir de 2030?")
    assert answer.data is not None
    assert answer.data.rows == ((0,),)


def test_date_extreme_must_be_projected_to_verify_zero(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE eventos ADD COLUMN data_contato TEXT")
        connection.execute("UPDATE eventos SET data_contato = '2024-01-01'")
    probe_sql = "SELECT COUNT(*) AS n FROM eventos ORDER BY MAX(date(data_contato))"
    assert store.run_sql(probe_sql).rows == ((2,),)
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": probe_sql, "purpose": "Contar eventos"}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE date(data_contato) >= '2030-01-01'",
                "purpose": "Contar período",
            },
        ),
        finish(answer="Nenhum evento a partir de 2030.", query_number=2),
        finish(answer="Nenhum evento a partir de 2030.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos a partir de 2030?")


@pytest.mark.parametrize(
    ("flag", "present_value", "missing_value"),
    (("resolvido", 1, 0), ("interagiu", 0, 1)),
)
def test_safe_flag_distribution_can_verify_zero(
    store, flag: str, present_value: int, missing_value: int
):
    with sqlite3.connect(store.path) as connection:
        connection.execute(f"ALTER TABLE eventos ADD COLUMN {flag} INTEGER")
        connection.execute(f"UPDATE eventos SET {flag} = ?", (present_value,))
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": f"SELECT {flag}, COUNT(*) FROM eventos GROUP BY {flag}",
                "purpose": "Verificar distribuição da flag",
            },
        ),
        (
            "run_sql",
            {
                "sql": f"SELECT COUNT(*) FROM eventos WHERE {flag} = {missing_value}",
                "purpose": "Contar valor ausente",
            },
        ),
        finish(answer="Nenhum evento corresponde à flag.", query_number=2),
    )

    answer = DataAnalyst(store, model).ask("Há eventos com a flag ausente?")
    assert answer.data is not None
    assert answer.data.rows == ((0,),)


def test_separate_unfiltered_probes_can_verify_category_and_date_zero(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE eventos ADD COLUMN data_contato TEXT")
        connection.execute("UPDATE eventos SET data_contato = '2024-01-01'")
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {"sql": "SELECT canal, COUNT(*) FROM eventos GROUP BY canal", "purpose": "Ver canais"},
        ),
        (
            "run_sql",
            {
                "sql": "SELECT MAX(date(data_contato)) AS fim FROM eventos",
                "purpose": "Ver data máxima",
            },
        ),
        (
            "run_sql",
            {
                "sql": (
                    "SELECT COUNT(*) FROM eventos "
                    "WHERE canal = 'App' AND date(data_contato) >= '2030-01-01'"
                ),
                "purpose": "Contar período e canal",
            },
        ),
        finish(answer="Nenhum evento no App a partir de 2030.", query_number=3),
    )

    answer = DataAnalyst(store, model).ask("Há eventos do App a partir de 2030?")
    assert answer.data is not None
    assert answer.data.rows == ((0,),)


def test_homonymous_column_in_joined_table_does_not_verify_zero(store):
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "CREATE TABLE outros_canais "
            "(id INTEGER PRIMARY KEY, evento_id INTEGER, canal TEXT);"
            "INSERT INTO outros_canais(evento_id, canal) VALUES (1, 'App'), (2, 'WhatsApp');"
        )
    joined_store = SQLiteReadOnly(
        store.path,
        category_fields={("eventos", "canal"), ("outros_canais", "canal")},
    )
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) FROM outros_canais GROUP BY canal",
                "purpose": "Ver outro canal",
            },
        ),
        (
            "run_sql",
            {
                "sql": (
                    "SELECT COUNT(*) FROM eventos AS e "
                    "JOIN outros_canais AS o ON o.evento_id = e.id "
                    "WHERE e.canal = 'App' AND e.canal = 'WhatsApp'"
                ),
                "purpose": "Contar eventos por canal",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(joined_store, model).ask("Há eventos nos dois canais?")


def test_joined_zero_accepts_probe_from_the_filtered_source_table(store):
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "CREATE TABLE outros_canais "
            "(id INTEGER PRIMARY KEY, evento_id INTEGER, canal TEXT);"
            "INSERT INTO outros_canais(evento_id, canal) VALUES (1, 'App'), (2, 'WhatsApp');"
        )
    joined_store = SQLiteReadOnly(
        store.path,
        category_fields={("eventos", "canal"), ("outros_canais", "canal")},
    )
    model = ScriptedModel(
        ("inspect_schema", {}),
        (
            "run_sql",
            {
                "sql": "SELECT canal, COUNT(*) FROM eventos GROUP BY canal",
                "purpose": "Ver canal dos eventos",
            },
        ),
        (
            "run_sql",
            {
                "sql": (
                    "SELECT COUNT(*) FROM eventos AS e "
                    "JOIN outros_canais AS o ON o.evento_id = e.id "
                    "WHERE e.canal = 'App' AND e.canal = 'WhatsApp'"
                ),
                "purpose": "Contar eventos por canal",
            },
        ),
        finish(answer="Nenhum evento pertence aos dois canais.", query_number=2),
    )

    answer = DataAnalyst(joined_store, model).ask("Há eventos nos dois canais?")
    assert answer.data is not None
    assert answer.data.rows == ((0,),)


def test_unrelated_table_count_does_not_verify_zero(store):
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "CREATE TABLE outros (id INTEGER PRIMARY KEY); INSERT INTO outros VALUES (1);"
        )
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM outros", "purpose": "Contar outros"}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_same_table_total_count_is_not_a_distribution_or_date_verification(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Contar total"}),
        (
            "run_sql",
            {
                "sql": "SELECT COUNT(*) FROM eventos WHERE canal = 'App' AND canal = 'WhatsApp'",
                "purpose": "Contar interseção",
            },
        ),
        finish(answer="Nenhum evento.", query_number=2),
        finish(answer="Nenhum evento.", query_number=2),
    )

    with pytest.raises(AgentError, match="confirmar|verifica"):
        DataAnalyst(store, model).ask("Há eventos simultaneamente nos dois canais?")


def test_refuses_final_answer_without_query_evidence(store):
    model = ScriptedModel(finish(answer="São 42 eventos."))

    with pytest.raises(AgentError, match="evidência|consulta"):
        DataAnalyst(store, model).ask("Quantos eventos existem?")


def test_accepts_clarification_without_claim_or_query(store):
    model = ScriptedModel(finish(answer="Qual período você quer analisar?", query_number=0))

    answer = DataAnalyst(store, model).ask("Como evoluiu?")

    assert answer.data is None
    assert answer.text.endswith("?")


def test_clarification_discards_untrusted_claim_assumptions_and_chart(store):
    untrusted_finish = finish(answer="Há 999 eventos. Qual período?", query_number=0, chart="bar")
    untrusted_finish[1]["assumptions"] = ["Há 999 eventos"]
    untrusted_finish[1]["x_column"] = "canal"
    untrusted_finish[1]["y_column"] = "total"
    model = ScriptedModel(untrusted_finish)

    answer = DataAnalyst(store, model).ask("Como evoluiu?")

    assert answer.text.endswith("?")
    assert "999" not in answer.text + answer.assumptions
    assert answer.assumptions == ""
    assert answer.data is None
    assert answer.chart.kind == "table"


def test_stops_after_step_limit(store):
    model = ScriptedModel(*[("inspect_schema", {}) for _ in range(3)])

    with pytest.raises(AgentError, match="limite"):
        DataAnalyst(store, model, max_turns=2).ask("Quantos?")


def test_stops_after_sql_error_limit(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELEC 1", "purpose": "Tentativa 1"}),
        ("run_sql", {"sql": "SELEC 2", "purpose": "Tentativa 2"}),
    )

    with pytest.raises(AgentError, match="limite"):
        DataAnalyst(store, model, max_sql_errors=2).ask("Quantos?")


def test_default_error_budget_allows_three_repairs_before_success(store):
    model = ScriptedModel(
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELEC 1", "purpose": "Tentativa 1"}),
        ("run_sql", {"sql": "SELEC 2", "purpose": "Tentativa 2"}),
        ("run_sql", {"sql": "SELEC 3", "purpose": "Tentativa 3"}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Corrigir"}),
        finish(),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos existem?")
    assert answer.data is not None
    assert answer.data.rows == ((2,),)


def test_rejects_empty_question(store):
    with pytest.raises(ValueError, match="pergunta"):
        DataAnalyst(store, ScriptedModel()).ask("  ")


def test_sql_requires_schema_discovery(store):
    model = ScriptedModel(
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Contar"}),
        ("inspect_schema", {}),
        ("run_sql", {"sql": "SELECT COUNT(*) FROM eventos", "purpose": "Contar"}),
        finish(),
    )

    answer = DataAnalyst(store, model).ask("Quantos eventos?")

    assert answer.steps[0].error
    assert answer.steps[2].row_count == 1
