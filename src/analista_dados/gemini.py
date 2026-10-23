"""Adaptador de function calling do Gemini pelo endpoint compatível com OpenAI."""

from __future__ import annotations

import copy
import json
import uuid
from importlib.resources import files
from typing import Any

from analista_dados import model as base
from analista_dados.models import ModelTurn, ToolCall

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


def _tools() -> list[dict[str, Any]]:
    """Reaproveita as ferramentas do agente no formato aceito pelo Gemini."""
    tools = []
    for tool in copy.deepcopy(base._TOOLS):
        parameters = tool["parameters"]
        parameters.pop("additionalProperties", None)
        if not parameters["properties"]:
            # O Gemini rejeita objeto sem propriedades; o valor é descartado depois.
            parameters = {"type": "object", "properties": {"_": {"type": "string"}}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": parameters,
                },
            }
        )
    return tools


class GeminiModel:
    def __init__(self, client, model: str = DEFAULT_GEMINI_MODEL) -> None:
        self.client = client
        self.model = model
        self.instructions = (
            files("analista_dados").joinpath("prompts", "system_v1.md").read_text(encoding="utf-8")
        )
        self._tools = _tools()

    def _messages(self, history: list) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.instructions}]
        for item in history:
            if item.get("role") == "user":
                messages.append({"role": "user", "content": item["content"]})
            elif item.get("type") == "function_call":
                call = {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {"name": item["name"], "arguments": item["arguments"]},
                }
                if item.get("extra_content"):
                    # O Gemini 3 exige devolver a assinatura de raciocínio da chamada.
                    call["extra_content"] = item["extra_content"]
                messages.append({"role": "assistant", "tool_calls": [call]})
            elif item.get("type") == "function_call_output":
                messages.append(
                    {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
                )
        return messages

    def next_turn(self, history: list) -> ModelTurn:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._messages(history),
                tools=self._tools,
                tool_choice="required",
                max_tokens=4000,
            )
        except Exception:
            raise base.ModelError(
                "Não foi possível consultar o modelo. Verifique a conexão, "
                "a chave e o limite da API."
            ) from None

        try:
            calls = response.choices[0].message.tool_calls or []
        except (AttributeError, IndexError, TypeError):
            raise base.ModelError(
                "O modelo não retornou uma chamada de ferramenta válida."
            ) from None
        if len(calls) != 1:
            raise base.ModelError("O modelo deve retornar exatamente uma chamada de ferramenta.")

        call = calls[0]
        name = call.function.name
        if name not in {"inspect_schema", "run_sql", "finish_answer"}:
            raise base.ModelError("O modelo chamou uma ferramenta desconhecida.")
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except (TypeError, ValueError):
            raise base.ModelError("O modelo enviou argumentos de ferramenta inválidos.") from None
        if name == "inspect_schema":
            arguments = {}
        call_id = call.id if isinstance(call.id, str) and call.id.strip() else None
        call_id = call_id or f"call_{uuid.uuid4().hex[:12]}"

        item = {
            "type": "function_call",
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
            "call_id": call_id,
            "extra_content": (getattr(call, "model_extra", None) or {}).get("extra_content"),
        }
        return ModelTurn(
            call=ToolCall(
                name=name,
                arguments=base._validate_arguments(name, arguments),
                call_id=call_id,
            ),
            output_items=[item],
        )
