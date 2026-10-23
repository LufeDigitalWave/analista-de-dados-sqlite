"""Seleção de provedor de modelo por variáveis de ambiente, sem rede."""

import pytest

from analista_dados import providers
from analista_dados.gemini import GeminiModel
from analista_dados.model import OpenAIModel


def test_gemini_is_the_default_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert providers.provider_name() == "gemini"
    assert providers.api_key_variable() == "GEMINI_API_KEY"


def test_openai_can_still_be_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    assert providers.provider_name() == "openai"
    assert providers.api_key_variable() == "OPENAI_API_KEY"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "outro")
    with pytest.raises(ValueError):
        providers.provider_name()


def test_model_names_follow_provider_and_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert providers.model_name("gemini") == "gemini-3.1-flash-lite"
    assert providers.model_name("openai") == "gpt-4.1"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-x")
    assert providers.model_name("gemini") == "gemini-x"


def test_has_api_key_reads_only_the_selected_variable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert providers.has_api_key() is False
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    assert providers.has_api_key() is True


def test_create_model_builds_the_matching_adapter(monkeypatch):
    created = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(providers, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("GEMINI_API_KEY", "chave-de-teste")
    gemini = providers.create_model("gemini")
    assert isinstance(gemini, GeminiModel)
    assert created["base_url"].startswith("https://generativelanguage.googleapis.com")
    assert created["api_key"] == "chave-de-teste"
    assert (created["timeout"], created["max_retries"]) == (60.0, 1)

    created.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "outra")
    assert isinstance(providers.create_model("openai"), OpenAIModel)
    assert "base_url" not in created
    assert (created["timeout"], created["max_retries"]) == (30.0, 1)
