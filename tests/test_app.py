"""Small application configuration checks without invoking the provider."""

from unittest.mock import MagicMock

import app
from analista_dados.models import Answer, ChartSpec, QueryResult, TraceStep


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


def test_comparison_chart_is_horizontal_and_keeps_table_visible(monkeypatch):
    fake_streamlit = MagicMock()
    fake_streamlit.selectbox.return_value = "Automático"
    monkeypatch.setattr(app, "st", fake_streamlit)
    answer = Answer(
        text="Totais por canal.",
        assumptions="",
        data=QueryResult(
            columns=("canal", "total", "observacao"),
            rows=(("Chat", 3, "primeiro"), ("E-mail", 2, "segundo")),
        ),
        chart=ChartSpec("bar", "canal", "total", ""),
        steps=(),
    )

    app.render_answer(answer)

    chart_kwargs = fake_streamlit.bar_chart.call_args.kwargs
    assert chart_kwargs["horizontal"] is True
    assert chart_kwargs["height"] >= 300
    assert "canal" in chart_kwargs["alt"] and "total" in chart_kwargs["alt"]
    assert fake_streamlit.dataframe.call_args.args[0].to_dict("records") == [
        {"canal": "Chat", "total": 3, "observacao": "primeiro"},
        {"canal": "E-mail", "total": 2, "observacao": "segundo"},
    ]


def test_monthly_line_chart_names_time_axis_and_has_descriptive_alt(monkeypatch):
    fake_streamlit = MagicMock()
    fake_streamlit.selectbox.return_value = "Automático"
    monkeypatch.setattr(app, "st", fake_streamlit)
    answer = Answer(
        text="Evolução mensal.",
        assumptions="",
        data=QueryResult(
            columns=("mes", "total"),
            rows=(("2024-07", 1), ("2025-07", 2)),
        ),
        chart=ChartSpec("line", "mes", "total", ""),
        steps=(),
    )

    app.render_answer(answer)

    chart_kwargs = fake_streamlit.line_chart.call_args.kwargs
    assert chart_kwargs["x_label"] == "Mês (ano)"
    assert "mes" in chart_kwargs["alt"] and "total" in chart_kwargs["alt"]
    fake_streamlit.caption.assert_any_call("Período no gráfico: 07/2024 a 07/2025.")


def test_initial_page_uses_responsive_sidebar(monkeypatch, tmp_path):
    fake_streamlit = MagicMock()
    fake_streamlit.session_state = {}
    fake_streamlit.button.return_value = False
    fake_streamlit.chat_input.return_value = None
    monkeypatch.setattr(app, "st", fake_streamlit)
    monkeypatch.setattr(app, "database_path", lambda: tmp_path / "missing.db")

    app.main()

    assert fake_streamlit.set_page_config.call_args.kwargs["initial_sidebar_state"] == "auto"


def test_answer_section_uses_second_level_heading(monkeypatch):
    fake_streamlit = MagicMock()
    monkeypatch.setattr(app, "st", fake_streamlit)
    answer = Answer(text="Resposta.", assumptions="", data=None, chart=ChartSpec(), steps=())

    app.render_answer(answer)

    fake_streamlit.markdown.assert_any_call("## Resposta")
