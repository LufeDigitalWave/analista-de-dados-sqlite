"""Contratos do adaptador de function calling, sem chamadas de rede."""

import traceback
from types import SimpleNamespace

import pytest

from analista_dados.model import ModelError, OpenAIModel


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def function_call(name="inspect_schema", arguments="{}", call_id="call_1"):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id)


def model_response(*items, status="completed"):
    return SimpleNamespace(status=status, output=list(items))


def test_one_function_call_is_translated_and_api_is_private_by_default():
    item = function_call()
    client = FakeClient(model_response(item))
    history = [{"role": "user", "content": "Quantos eventos?"}]

    turn = OpenAIModel(client).next_turn(history)

    assert turn.call.name == "inspect_schema"
    assert turn.call.arguments == {}
    assert turn.call.call_id == "call_1"
    assert turn.output_items == [item]
    kwargs = client.responses.kwargs
    assert kwargs["model"] == "gpt-4.1"
    assert kwargs["input"] == history
    assert kwargs["store"] is False
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["tool_choice"] == "required"
    assert {tool["name"] for tool in kwargs["tools"]} == {
        "inspect_schema",
        "run_sql",
        "finish_answer",
    }
    for tool in kwargs["tools"]:
        assert tool["strict"] is True
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


def test_sql_arguments_are_decoded_without_mutating_input_history():
    history = [{"role": "user", "content": "Conte eventos"}]
    item = function_call(
        "run_sql",
        '{"sql":"SELECT COUNT(*) AS total FROM eventos","purpose":"Contar eventos"}',
    )
    client = FakeClient(model_response(item))

    turn = OpenAIModel(client, model="gpt-4.1-mini").next_turn(history)

    assert turn.call.arguments == {
        "sql": "SELECT COUNT(*) AS total FROM eventos",
        "purpose": "Contar eventos",
    }
    assert client.responses.kwargs["model"] == "gpt-4.1-mini"
    assert history == [{"role": "user", "content": "Conte eventos"}]


def test_finish_schema_requires_a_valid_evidence_index_and_complete_fields():
    item = function_call(
        "finish_answer",
        '{"answer":"Existem 2 eventos.","query_number":1,'
        '"visualization":"table","x_column":"","y_column":"",'
        '"series_column":"","assumptions":[]}',
    )
    client = FakeClient(model_response(item))

    turn = OpenAIModel(client).next_turn([])

    assert turn.call.arguments["query_number"] == 1
    tools = {tool["name"]: tool for tool in client.responses.kwargs["tools"]}
    assert set(tools["finish_answer"]["parameters"]["required"]) == {
        "answer",
        "query_number",
        "visualization",
        "x_column",
        "y_column",
        "series_column",
        "assumptions",
    }


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (model_response(status="incomplete"), "incompleta"),
        (model_response(), "ferramenta"),
        (
            model_response(function_call(), function_call(call_id="call_2")),
            "ferramenta",
        ),
        (model_response(function_call(name="unknown")), "desconhecida"),
        (model_response(function_call(name=[])), "desconhecida"),
        (model_response(function_call(arguments="{bad json")), "argumentos"),
        (model_response(function_call(arguments="[]")), "argumentos"),
        (model_response(function_call(call_id="")), "identificador"),
        (
            model_response(
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="refusal", refusal="não posso")],
                )
            ),
            "recusou",
        ),
    ],
)
def test_invalid_provider_outputs_fail_with_controlled_error(response, reason):
    with pytest.raises(ModelError, match=reason):
        OpenAIModel(FakeClient(response)).next_turn([])


@pytest.mark.parametrize(
    "arguments",
    [
        '{"sql":"SELECT 1"}',
        '{"sql":4,"purpose":"Verificar"}',
        '{"sql":"SELECT 1","purpose":"Verificar","extra":true}',
        '{"answer":"Dois","query_number":1}',
        '{"answer":"Dois","query_number":true,"visualization":"table",'
        '"x_column":"","y_column":"","series_column":"","assumptions":[]}',
        '{"answer":"Dois","query_number":1,"visualization":[],"x_column":"",'
        '"y_column":"","series_column":"","assumptions":[]}',
    ],
)
def test_malformed_tool_arguments_are_rejected(arguments):
    name = "finish_answer" if '"answer"' in arguments else "run_sql"
    with pytest.raises(ModelError, match="argumentos"):
        OpenAIModel(FakeClient(model_response(function_call(name, arguments)))).next_turn([])


def test_api_exception_is_sanitized():
    client = FakeClient(RuntimeError("sk-FAKE-SECRET: HTTP 429"))

    with pytest.raises(ModelError) as exc:
        OpenAIModel(client).next_turn([])

    assert "sk-FAKE-SECRET" not in str(exc.value)
    assert "429" not in str(exc.value)
    assert "sk-FAKE-SECRET" not in "".join(traceback.format_exception(exc.value))
