"""Prepare trustworthy tables and charts from executed SQL results."""

import re
from dataclasses import dataclass

import pandas as pd

from analista_dados.models import ChartSpec, QueryResult

_TEMPORAL_PARTS = {"ano", "mes", "mês", "data", "dia", "semana", "trimestre"}


def _is_temporal_column(column: str) -> bool:
    return any(part in _TEMPORAL_PARTS for part in re.split(r"[\W_]+", column.casefold()))


@dataclass(frozen=True)
class PreparedVisualization:
    kind: str
    frame: pd.DataFrame
    table: pd.DataFrame
    x_column: str | None
    y_columns: tuple[str, ...]


def prepare_visualization(
    result: QueryResult, suggestion: ChartSpec, override: str = "auto"
) -> PreparedVisualization:
    """Use a chart only when its axes match complete, numeric query results."""
    table = pd.DataFrame(result.rows, columns=result.columns)
    fallback = PreparedVisualization("table", table, table, None, ())
    kind = suggestion.kind if override == "auto" else override
    if kind not in {"bar", "line"} or result.truncated or table.empty:
        return fallback

    x_column = suggestion.x_column
    y_column = suggestion.y_column
    series_column = suggestion.series_column
    if override in {"bar", "line"}:
        if override == "line" and not x_column:
            x_column = next(
                (
                    column
                    for column in table.columns
                    if column not in {y_column, series_column} and _is_temporal_column(column)
                ),
                "",
            )
        if not x_column:
            x_column = next(
                (
                    column
                    for column in table.columns
                    if column not in {y_column, series_column}
                    and not pd.api.types.is_numeric_dtype(table[column])
                ),
                "",
            )
        if not x_column:
            x_column = next(
                (
                    column
                    for column in table.columns
                    if column not in {y_column, series_column} and _is_temporal_column(column)
                ),
                "",
            )
        if not y_column:
            y_column = next(
                (
                    column
                    for column in table.columns
                    if column not in {x_column, series_column}
                    and pd.api.types.is_numeric_dtype(table[column])
                ),
                "",
            )
    if (
        len(set(result.columns)) != len(result.columns)
        or x_column not in table.columns
        or y_column not in table.columns
        or x_column == y_column
        or table[x_column].isna().any()
        or not pd.api.types.is_numeric_dtype(table[y_column])
    ):
        return fallback

    if override == "line" and _is_temporal_column(x_column):
        if not series_column:
            candidates = [
                column
                for column in table.columns
                if column not in {x_column, y_column}
                and not pd.api.types.is_numeric_dtype(table[column])
            ]
            if len(candidates) > 1:
                return fallback
            if candidates:
                series_column = candidates[0]

    if not series_column:
        if kind == "line" and table[x_column].duplicated().any():
            return fallback
        frame = table[[x_column, y_column]].copy()
        return PreparedVisualization(kind, frame, table, x_column, (y_column,))

    if (
        series_column not in table.columns
        or series_column in {x_column, y_column}
        or table[series_column].isna().any()
        or table[[x_column, series_column]].duplicated().any()
    ):
        return fallback

    try:
        frame = table.pivot(index=x_column, columns=series_column, values=y_column).reset_index()
    except (TypeError, ValueError):
        # Labels returned by SQL can collide with the index name or be unsortable.
        return fallback
    names = [str(name) for name in frame.columns]
    if len(set(names)) != len(names):
        return fallback
    frame.columns = names
    y_columns = tuple(name for name in names if name != x_column)
    if not y_columns:
        return fallback
    return PreparedVisualization(kind, frame, table, x_column, y_columns)
