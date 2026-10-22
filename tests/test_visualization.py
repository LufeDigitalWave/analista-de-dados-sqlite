"""Contratos de apresentação de resultados SQL, sem chamadas ao modelo."""

from analista_dados.models import ChartSpec, QueryResult
from analista_dados.visualization import prepare_visualization


def _result(columns, rows, *, truncated=False):
    return QueryResult(
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in rows),
        truncated=truncated,
        elapsed_ms=1.0,
    )


def test_bar_chart_uses_only_selected_axes_but_keeps_full_table():
    result = _result(
        ("canal", "total", "observacao"),
        (("Chat", 3, "primeiro"), ("E-mail", 2, "segundo")),
    )

    view = prepare_visualization(result, ChartSpec("bar", "canal", "total", ""))

    assert view.kind == "bar"
    assert list(view.frame.columns) == ["canal", "total"]
    assert view.frame.to_dict("records") == [
        {"canal": "Chat", "total": 3},
        {"canal": "E-mail", "total": 2},
    ]
    assert list(view.table.columns) == ["canal", "total", "observacao"]
    assert view.x_column == "canal"
    assert view.y_columns == ("total",)


def test_line_chart_pivots_channel_series_without_losing_source_table():
    result = _result(
        ("mes", "canal", "total"),
        (
            ("2025-01", "Chat", 2),
            ("2025-01", "E-mail", 1),
            ("2025-02", "Chat", 3),
            ("2025-02", "E-mail", 4),
        ),
    )

    view = prepare_visualization(result, ChartSpec("line", "mes", "total", "canal"))

    assert view.kind == "line"
    assert list(view.frame.columns)[0] == "mes"
    assert set(view.y_columns) == {"Chat", "E-mail"}
    indexed = view.frame.set_index("mes")
    assert indexed.loc["2025-01", "Chat"] == 2
    assert indexed.loc["2025-02", "E-mail"] == 4
    assert len(view.table) == 4


def test_unknown_axis_falls_back_to_table():
    result = _result(("canal", "total"), (("Chat", 3),))

    view = prepare_visualization(result, ChartSpec("bar", "canal", "inexistente", ""))

    assert view.kind == "table"
    assert view.frame.equals(view.table)


def test_non_numeric_measure_falls_back_to_table():
    result = _result(("canal", "status"), (("Chat", "alto"),))

    view = prepare_visualization(result, ChartSpec("bar", "canal", "status", ""))

    assert view.kind == "table"


def test_truncated_result_does_not_render_misleading_chart():
    result = _result(("canal", "total"), (("Chat", 3),), truncated=True)

    view = prepare_visualization(result, ChartSpec("bar", "canal", "total", ""))

    assert view.kind == "table"
    assert view.table.to_dict("records") == [{"canal": "Chat", "total": 3}]


def test_manual_override_can_switch_chart_type_or_force_table():
    result = _result(("canal", "total"), (("Chat", 3), ("E-mail", 2)))
    suggested = ChartSpec("bar", "canal", "total", "")

    line = prepare_visualization(result, suggested, override="line")
    table = prepare_visualization(result, suggested, override="table")

    assert line.kind == "line"
    assert line.y_columns == ("total",)
    assert table.kind == "table"


def test_manual_bar_override_infers_category_and_measure_from_table_suggestion():
    result = _result(
        ("total", "canal", "gasto"),
        ((3, "Chat", 10.0), (2, "E-mail", 20.0)),
    )

    view = prepare_visualization(result, ChartSpec(), override="bar")

    assert view.kind == "bar"
    assert view.x_column == "canal"
    assert view.y_columns == ("total",)
    assert view.frame.to_dict("records") == [
        {"canal": "Chat", "total": 3},
        {"canal": "E-mail", "total": 2},
    ]


def test_manual_line_override_accepts_numeric_year_as_dimension():
    result = _result(("ano", "total"), ((2024, 5), (2025, 8)))

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "line"
    assert view.x_column == "ano"
    assert view.y_columns == ("total",)


def test_manual_line_override_prefers_temporal_axis_after_category():
    result = _result(
        ("canal", "mes", "total"),
        (("Chat", "2025-01", 2), ("Chat", "2025-02", 3)),
    )

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "line"
    assert view.x_column == "mes"


def test_manual_line_override_infers_unique_channel_series_for_temporal_trend():
    result = _result(
        ("canal", "mes", "total"),
        (
            ("Chat", "2025-01", 2),
            ("E-mail", "2025-01", 1),
            ("Chat", "2025-02", 3),
            ("E-mail", "2025-02", 4),
        ),
    )

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "line"
    assert view.x_column == "mes"
    assert set(view.y_columns) == {"Chat", "E-mail"}
    assert view.frame.set_index("mes").loc["2025-02", "E-mail"] == 4


def test_manual_line_override_infers_series_with_explicit_temporal_axes():
    result = _result(
        ("mes", "canal", "total"),
        (
            ("2025-01", "Chat", 2),
            ("2025-01", "E-mail", 1),
            ("2025-02", "Chat", 3),
            ("2025-02", "E-mail", 4),
        ),
    )
    suggestion = ChartSpec("table", "mes", "total", "")

    view = prepare_visualization(result, suggestion, override="line")

    assert view.kind == "line"
    assert view.x_column == "mes"
    assert set(view.y_columns) == {"Chat", "E-mail"}
    assert view.frame.set_index("mes").loc["2025-02", "E-mail"] == 4


def test_line_without_series_rejects_duplicate_x_values():
    result = _result(("mes", "total"), (("2025-01", 2), ("2025-01", 3)))

    manual = prepare_visualization(result, ChartSpec("table", "mes", "total", ""), "line")
    automatic = prepare_visualization(result, ChartSpec("line", "mes", "total", ""))

    assert manual.kind == "table"
    assert automatic.kind == "table"


def test_manual_line_override_rejects_ambiguous_series_candidates():
    result = _result(
        ("canal", "regiao", "mes", "total"),
        (("Chat", "Sul", "2025-01", 2), ("E-mail", "Sudeste", "2025-01", 1)),
    )

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "table"


def test_manual_line_override_rejects_repeated_period_category_pairs():
    result = _result(
        ("canal", "mes", "total"),
        (("Chat", "2025-01", 2), ("Chat", "2025-01", 3)),
    )

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "table"


def test_manual_line_override_recognizes_temporal_prefix_and_suffix():
    for temporal_column in ("mes_compra", "compra_mes", "data_compra"):
        result = _result(
            ("canal", temporal_column, "total"),
            (("Chat", "2025-01", 2), ("Chat", "2025-02", 3)),
        )

        view = prepare_visualization(result, ChartSpec(), override="line")

        assert view.kind == "line"
        assert view.x_column == temporal_column


def test_manual_override_preserves_explicit_series_when_inferring_axes():
    result = _result(
        ("canal", "mes", "total"),
        (("Chat", "2025-01", 2), ("E-mail", "2025-01", 1)),
    )

    view = prepare_visualization(
        result, ChartSpec(kind="table", series_column="canal"), override="line"
    )

    assert view.kind == "line"
    assert view.x_column == "mes"
    assert set(view.y_columns) == {"Chat", "E-mail"}


def test_manual_override_keeps_truncated_results_as_table():
    result = _result(("canal", "total"), (("Chat", 3),), truncated=True)

    view = prepare_visualization(result, ChartSpec(), override="bar")

    assert view.kind == "table"


def test_manual_override_does_not_guess_axis_from_two_numeric_measures():
    result = _result(("total", "media"), ((5, 2.5), (8, 4.0)))

    view = prepare_visualization(result, ChartSpec(), override="line")

    assert view.kind == "table"


def test_repeated_x_series_pair_falls_back_instead_of_aggregating_silently():
    result = _result(
        ("mes", "canal", "total"),
        (("2025-01", "Chat", 2), ("2025-01", "Chat", 3)),
    )

    view = prepare_visualization(result, ChartSpec("line", "mes", "total", "canal"))

    assert view.kind == "table"
    assert len(view.table) == 2


def test_series_label_matching_x_axis_falls_back_without_crashing():
    result = _result(
        ("mes", "canal", "total"),
        (("2025-01", "mes", 2),),
    )

    view = prepare_visualization(result, ChartSpec("line", "mes", "total", "canal"))

    assert view.kind == "table"
    assert view.table.to_dict("records") == [{"mes": "2025-01", "canal": "mes", "total": 2}]
