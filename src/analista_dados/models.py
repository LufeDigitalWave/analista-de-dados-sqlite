"""Small data contracts shared by the agent, database and presentation layers."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool = False
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class ModelTurn:
    call: ToolCall
    output_items: list[Any]


@dataclass(frozen=True)
class TraceStep:
    action: str
    purpose: str = ""
    sql: str | None = None
    error: str | None = None
    row_count: int | None = None


@dataclass(frozen=True)
class ChartSpec:
    kind: str = "table"
    x_column: str = ""
    y_column: str = ""
    series_column: str = ""


@dataclass(frozen=True)
class Answer:
    text: str
    assumptions: str
    data: QueryResult | None
    chart: ChartSpec
    steps: tuple[TraceStep, ...]
