"""Small application configuration checks without invoking the provider."""

import app


def test_api_client_has_bounded_request_time_and_retries(monkeypatch):
    observed = {}
    client = object()

    def fake_openai(**kwargs):
        observed.update(kwargs)
        return client

    monkeypatch.setattr(app, "OpenAI", fake_openai)

    assert app.create_openai_client() is client
    assert observed == {"timeout": 30.0, "max_retries": 1}
