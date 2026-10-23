"""Contratos do adaptador Gemini (endpoint compatível com OpenAI), sem rede."""

import json
from types import SimpleNamespace

import pytest

from analista_dados.gemini import GeminiModel
from analista_dados.model import ModelError


class FakeCompletions:
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
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def tool_call(name="inspect_schema", arguments="{}", call_id="call_1", extra=None):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
        model_extra={"extra_content": extra} if extra else {},
    )


def completion(*calls):
    message = SimpleNamespace(tool_calls=list(calls))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_tool_call_is_translated_and_tools_use_gemini_compatible_schema():
    client = FakeClient(completion(tool_call()))
    history = [{"role": "user", "content": "Quantos eventos?"}]

    turn = GeminiModel(client, "gemini-test").next_turn(history)

    assert turn.call.name == "inspect_schema"
    assert turn.call.arguments == {}
    assert turn.call.call_id == "call_1"
    kwargs = client.completions.kwargs
    assert kwargs["model"] == "gemini-test"
    assert kwargs["tool_choice"] == "required"
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1] == {"role": "user", "content": "Quantos eventos?"}
    names = {tool["function"]["name"] for tool in kwargs["tools"]}
    assert names == {"inspect_schema", "run_sql", "finish_answer"}
    for tool in kwargs["tools"]:
        parameters = tool["function"]["parameters"]
        assert "additionalProperties" not in parameters
        assert parameters["properties"]


def test_thought_signature_is_kept_and_sent_back_on_the_next_turn():
    signature = {"google": {"thought_signature": "sig-1"}}
    first = FakeClient(completion(tool_call(extra=signature)))
    turn = GeminiModel(first).next_turn([{"role": "user", "content": "Oi"}])

    history = [
        {"role": "user", "content": "Oi"},
        *turn.output_items,
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true}'},
    ]
    second = FakeClient(completion(tool_call("run_sql", '{"sql":"SELECT 1","purpose":"t"}')))
    GeminiModel(second).next_turn(history)

    messages = second.completions.kwargs["messages"]
    assistant = messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["extra_content"] == signature
    assert assistant["tool_calls"][0]["function"]["name"] == "inspect_schema"
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}


def test_run_sql_arguments_are_validated_like_the_openai_adapter():
    sql_call = tool_call("run_sql", json.dumps({"sql": "SELECT 1", "purpose": "Teste"}))
    turn = GeminiModel(FakeClient(completion(sql_call))).next_turn([])
    assert turn.call.arguments == {"sql": "SELECT 1", "purpose": "Teste"}

    bad = tool_call("run_sql", json.dumps({"sql": "SELECT 1"}))
    with pytest.raises(ModelError):
        GeminiModel(FakeClient(completion(bad))).next_turn([])


@pytest.mark.parametrize(
    "response",
    [completion(), completion(tool_call(), tool_call(call_id="call_2"))],
)
def test_requires_exactly_one_tool_call(response):
    with pytest.raises(ModelError):
        GeminiModel(FakeClient(response)).next_turn([])


def test_provider_failure_becomes_controlled_error_without_leaking_details():
    with pytest.raises(ModelError) as raised:
        GeminiModel(FakeClient(RuntimeError("chave secreta vazou"))).next_turn([])
    assert "secreta" not in str(raised.value)


def test_unknown_tool_is_rejected():
    with pytest.raises(ModelError):
        GeminiModel(FakeClient(completion(tool_call("drop_tables")))).next_turn([])
