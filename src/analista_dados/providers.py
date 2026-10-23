"""Escolha do provedor de modelo (Gemini por padrão, OpenAI opcional)."""

from __future__ import annotations

import os

from openai import OpenAI

from analista_dados.gemini import DEFAULT_GEMINI_MODEL, GEMINI_BASE_URL, GeminiModel
from analista_dados.model import OpenAIModel

_KEY_VARIABLES = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
_DEFAULT_MODELS = {"gemini": DEFAULT_GEMINI_MODEL, "openai": "gpt-4.1"}


def provider_name() -> str:
    name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if name not in _KEY_VARIABLES:
        raise ValueError("LLM_PROVIDER deve ser 'gemini' ou 'openai'.")
    return name


def api_key_variable(provider: str | None = None) -> str:
    return _KEY_VARIABLES[provider or provider_name()]


def has_api_key() -> bool:
    return bool(os.getenv(api_key_variable()))


def model_name(provider: str | None = None) -> str:
    chosen = provider or provider_name()
    return os.getenv(f"{chosen.upper()}_MODEL", _DEFAULT_MODELS[chosen])


def create_model(provider: str | None = None):
    """Cria o adaptador com timeout limitado, para uma rede parada não travar a interface."""
    chosen = provider or provider_name()
    if chosen == "gemini":
        client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url=GEMINI_BASE_URL,
            timeout=60.0,
            max_retries=1,
        )
        return GeminiModel(client, model_name(chosen))
    client = OpenAI(timeout=30.0, max_retries=1)
    return OpenAIModel(client, model=model_name(chosen))
