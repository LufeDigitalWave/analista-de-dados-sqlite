"""Adaptador de uma rodada de function calling da OpenAI Responses API."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from analista_dados.models import ModelTurn, ToolCall

_TOOLS = [
    {
        "type": "function",
        "name": "inspect_schema",
        "description": "Descobre tabelas, colunas e chaves estrangeiras do SQLite, sem ler dados.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_sql",
        "description": (
            "Executa uma consulta SQLite somente leitura. Informe o SQL e uma frase "
            "curta descrevendo o objetivo operacional da consulta."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Uma única consulta SELECT ou WITH."},
                "purpose": {
                    "type": "string",
                    "description": "Objetivo operacional curto, sem raciocínio interno.",
                },
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "finish_answer",
        "description": (
            "Conclui com resposta em português e indica qual resultado SQL sustenta "
            "a tabela ou o gráfico. Use índice zero apenas para pedir esclarecimento."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Resposta factual em português."},
                "query_number": {
                    "type": "integer",
                    "description": (
                        "Número 1-based da consulta SQL exibida; 0 só para esclarecimento."
                    ),
                },
                "visualization": {
                    "type": "string",
                    "enum": ["table", "bar", "line"],
                    "description": "Visual mais adequado ao resultado escolhido.",
                },
                "x_column": {
                    "type": "string",
                    "description": "Coluna do eixo X, ou string vazia para tabela.",
                },
                "y_column": {
                    "type": "string",
                    "description": "Coluna numérica do eixo Y, ou string vazia para tabela.",
                },
                "series_column": {
                    "type": "string",
                    "description": "Coluna de série, ou string vazia quando não aplicável.",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Interpretações relevantes, como período ou denominador.",
                },
            },
            "required": [
                "answer",
                "query_number",
                "visualization",
                "x_column",
                "y_column",
                "series_column",
                "assumptions",
            ],
            "additionalProperties": False,
        },
    },
]


def _field(item: Any, name: str, default: Any = None) -> Any:
    """Aceita objetos tipados do SDK e dicionários de testes."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _has_refusal(output: list[Any]) -> bool:
    for item in output:
        for content in _field(item, "content", []) or []:
            if _field(content, "type") == "refusal":
                return True
    return False


def _validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ModelError("O modelo enviou argumentos de ferramenta inválidos.")

    if name == "inspect_schema":
        valid = not arguments
    elif name == "run_sql":
        valid = set(arguments) == {"sql", "purpose"} and all(
            isinstance(arguments[key], str) and arguments[key].strip() for key in arguments
        )
    else:
        valid = (
            set(arguments)
            == {
                "answer",
                "query_number",
                "visualization",
                "x_column",
                "y_column",
                "series_column",
                "assumptions",
            }
            and isinstance(arguments["answer"], str)
            and bool(arguments["answer"].strip())
            and type(arguments["query_number"]) is int
            and arguments["query_number"] >= 0
            and isinstance(arguments["visualization"], str)
            and arguments["visualization"] in {"table", "bar", "line"}
            and all(
                isinstance(arguments[key], str) for key in ("x_column", "y_column", "series_column")
            )
            and isinstance(arguments["assumptions"], list)
            and all(isinstance(value, str) for value in arguments["assumptions"])
        )
    if not valid:
        raise ModelError("O modelo enviou argumentos de ferramenta inválidos.")
    return arguments


class ModelError(RuntimeError):
    """Falha controlada na resposta do provedor de modelo."""


class OpenAIModel:
    def __init__(self, client, model: str = "gpt-4.1") -> None:
        self.client = client
        self.model = model
        self.instructions = (
            files("analista_dados").joinpath("prompts", "system_v1.md").read_text(encoding="utf-8")
        )

    def next_turn(self, history: list) -> ModelTurn:
        try:
            response = self.client.responses.create(
                model=self.model,
                input=history,
                instructions=self.instructions,
                tools=_TOOLS,
                tool_choice="required",
                parallel_tool_calls=False,
                max_output_tokens=1600,
                store=False,
            )
        except Exception:
            raise ModelError(
                "Não foi possível consultar o modelo. Verifique a conexão, "
                "a chave e o limite da API."
            ) from None

        if _field(response, "status") != "completed":
            raise ModelError("A resposta do modelo veio incompleta. Tente novamente.")

        output = _field(response, "output")
        if not isinstance(output, list):
            raise ModelError("O modelo não retornou uma chamada de ferramenta válida.")
        if _has_refusal(output):
            raise ModelError("O modelo recusou a solicitação.")

        calls = [item for item in output if _field(item, "type") == "function_call"]
        if len(calls) != 1:
            raise ModelError("O modelo deve retornar exatamente uma chamada de ferramenta.")
        call = calls[0]
        name = _field(call, "name")
        if not isinstance(name, str) or name not in {
            "inspect_schema",
            "run_sql",
            "finish_answer",
        }:
            raise ModelError("O modelo chamou uma ferramenta desconhecida.")
        call_id = _field(call, "call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ModelError("A chamada do modelo veio sem identificador válido.")
        raw_arguments = _field(call, "arguments")
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError):
            raise ModelError("O modelo enviou argumentos de ferramenta inválidos.") from None

        return ModelTurn(
            call=ToolCall(
                name=name, arguments=_validate_arguments(name, arguments), call_id=call_id
            ),
            output_items=output,
        )
