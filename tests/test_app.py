"""Small application configuration checks without invoking the provider."""

from unittest.mock import MagicMock

import app
from analista_dados.models import Answer, ChartSpec, TraceStep


def test_api_client_has_bounded_request_time_and_retries(monkeypatch):
    observed = {}
    client = object()

    def fake_openai(**kwargs):
        observed.update(kwargs)
        return client

    monkeypatch.setattr(app, "OpenAI", fake_openai)

    assert app.create_openai_client() is client
    assert observed == {"timeout": 30.0, "max_retries": 1}


def test_model_supplied_text_never_uses_markdown_renderers(monkeypatch):
    payload = "![x](//example.invalid/EXAMPLE_PRIVATE)"
    fake_streamlit = MagicMock()
    monkeypatch.setattr(app, "st", fake_streamlit)
    answer = Answer(
        text=payload,
        assumptions=payload,
        data=None,
        chart=ChartSpec(),
        steps=(TraceStep(action="run_sql", purpose=payload, error=payload),),
    )

    app.render_answer(answer)

    for method in ("markdown", "write", "caption", "info", "error"):
        rendered = str(getattr(fake_streamlit, method).call_args_list)
        assert payload not in rendered, method
    assert str(fake_streamlit.text.call_args_list).count(payload) == 4
