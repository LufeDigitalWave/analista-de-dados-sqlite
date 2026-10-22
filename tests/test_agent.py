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
