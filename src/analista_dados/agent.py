"""Bounded orchestration of schema discovery, SQL and a sourced answer."""

import json
from typing import Protocol

import sqlglot
from sqlglot import exp

from .database import QueryError, SQLiteReadOnly
from .models import Answer, ChartSpec, ModelTurn, QueryResult, TraceStep


class AgentError(RuntimeError):
    """The question could not be answered within the safe execution limits."""

    def __init__(self, message: str, steps: tuple[TraceStep, ...] = ()) -> None:
        super().__init__(message)
        self.steps = steps


class _UnverifiedEmptyResult(AgentError):
    """A zero result needs another independent check before it can support an answer."""


class Model(Protocol):
    def next_turn(self, history: list) -> ModelTurn: ...


class DataAnalyst:
    """One model, three tools and finite attempts per question."""

    def __init__(
        self,
        db: SQLiteReadOnly,
        model: Model,
        max_turns: int = 8,
        max_sql_errors: int = 4,
    ) -> None:
        if max_turns < 1 or max_sql_errors < 1:
            raise ValueError("Os limites de execução devem ser positivos.")
        self.db = db
        self.model = model
        self.max_turns = max_turns
        self.max_sql_errors = max_sql_errors

    def ask(self, question: str) -> Answer:
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("Digite uma pergunta antes de consultar o banco.")
        if len(clean_question) > 4_000:
            raise ValueError("A pergunta excede o limite de 4.000 caracteres.")

        history: list = [{"role": "user", "content": clean_question}]
        steps: list[TraceStep] = []
        successful_queries: list[QueryResult] = []
        successful_sql: list[str] = []
        schema_seen = False
        sql_errors = 0
        empty_finishes = 0

        for _ in range(self.max_turns):
            try:
                turn = self.model.next_turn(history)
            except Exception as exc:
                raise AgentError(
                    "Não foi possível consultar o modelo. Verifique a conexão e a configuração.",
                    tuple(steps),
                ) from exc

            history.extend(turn.output_items)
            call = turn.call

            if call.name == "finish_answer":
                try:
                    return self._finish(call.arguments, successful_queries, successful_sql, steps)
                except _UnverifiedEmptyResult as exc:
                    empty_finishes += 1
                    if empty_finishes >= 2:
                        raise AgentError(
                            "Não foi possível confirmar o resultado vazio após nova tentativa.",
                            tuple(steps),
                        ) from exc
                    payload = {"ok": False, "error": str(exc)}

            elif call.name == "inspect_schema":
                try:
                    schema = self.db.inspect_schema()
                except Exception as exc:
                    raise AgentError(
                        "Não foi possível ler o esquema do banco.", tuple(steps)
                    ) from exc
                schema_seen = True
                steps.append(
                    TraceStep(
                        action="inspect_schema",
                        purpose="Descobrir tabelas, colunas e relações disponíveis",
                    )
                )
                payload = {"ok": True, "schema": schema}

            elif call.name == "run_sql":
                sql = call.arguments.get("sql")
                purpose = call.arguments.get("purpose")
                if not isinstance(sql, str) or not isinstance(purpose, str):
                    raise AgentError("A ferramenta SQL recebeu argumentos inválidos.", tuple(steps))
                try:
                    if not schema_seen:
                        raise QueryError("Consulte o esquema antes de executar SQL.")
                    result = self.db.run_sql(sql)
                except QueryError as exc:
                    sql_errors += 1
                    steps.append(
                        TraceStep(action="run_sql", purpose=purpose, sql=sql, error=str(exc))
                    )
                    payload = {"ok": False, "error": str(exc)}
                    if sql_errors >= self.max_sql_errors:
                        raise AgentError(
                            "O limite de tentativas SQL inválidas foi atingido.", tuple(steps)
                        ) from exc
                else:
                    successful_queries.append(result)
                    successful_sql.append(sql)
                    query_number = len(successful_queries)
                    steps.append(
                        TraceStep(
                            action="run_sql",
                            purpose=purpose,
                            sql=sql,
                            row_count=len(result.rows),
                        )
                    )
                    payload = {
                        "ok": True,
                        "query_number": query_number,
                        "columns": result.columns,
                        "rows": result.rows,
                        "truncated": result.truncated,
                        "elapsed_ms": result.elapsed_ms,
                    }
            else:
                raise AgentError("O modelo solicitou uma ferramenta desconhecida.", tuple(steps))

            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(payload, ensure_ascii=False, default=_json_safe),
                }
            )

        raise AgentError("O limite de passos foi atingido antes da resposta final.", tuple(steps))

    @staticmethod
    def _finish(
        arguments: dict,
        successful_queries: list[QueryResult],
        successful_sql: list[str],
        steps: list[TraceStep],
    ) -> Answer:
        answer = arguments.get("answer")
        query_number = arguments.get("query_number")
        if not isinstance(answer, str) or not answer.strip():
            raise AgentError("O modelo não forneceu uma resposta final válida.", tuple(steps))
        if isinstance(query_number, bool) or not isinstance(query_number, int):
            raise AgentError("O modelo não indicou a consulta de evidência.", tuple(steps))

        if query_number == 0:
            if not answer.strip().endswith("?"):
                raise AgentError("Resposta sem evidência deve pedir esclarecimento.", tuple(steps))
            result = None
        elif 1 <= query_number <= len(successful_queries):
            result = successful_queries[query_number - 1]
            if _is_empty_evidence(result) and (
                query_number != len(successful_queries)
                or not _has_related_verification(
                    successful_queries[: query_number - 1],
                    successful_sql[: query_number - 1],
                    successful_sql[query_number - 1],
                )
            ):
                raise _UnverifiedEmptyResult(
                    "Resultado vazio ou zero ainda não confirmado. Revise filtros, valores "
                    "categóricos e período; consulte distribuição ou datas da mesma fonte "
                    "e execute novamente antes de concluir.",
                    tuple(steps),
                )
        else:
            raise AgentError("A resposta não aponta para uma consulta de evidência.", tuple(steps))

        assumptions = arguments.get("assumptions", [])
        if not isinstance(assumptions, list) or not all(
            isinstance(item, str) for item in assumptions
        ):
            raise AgentError("O modelo forneceu pressupostos inválidos.", tuple(steps))

        kind = arguments.get("visualization", "table")
        if kind not in {"table", "bar", "line"}:
            kind = "table"
        chart = ChartSpec(
            kind=kind,
            x_column=_text_or_empty(arguments.get("x_column")),
            y_column=_text_or_empty(arguments.get("y_column")),
            series_column=_text_or_empty(arguments.get("series_column")),
        )
        return Answer(
            text=answer.strip(),
            assumptions="; ".join(item.strip() for item in assumptions if item.strip()),
            data=result,
            chart=chart,
            steps=tuple(steps),
        )


def _text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _is_empty_evidence(result: QueryResult) -> bool:
    if not result.rows:
        return True
    if len(result.rows) != 1:
        return False
    values = result.rows[0]
    return bool(values) and all(
        value is None or (isinstance(value, (int, float)) and value == 0) for value in values
    )


def _has_related_verification(
    previous_results: list[QueryResult], previous_sql: list[str], selected_sql: str
) -> bool:
    """Require a related distribution or date-bound probe before a zero claim."""
    try:
        target = sqlglot.parse_one(selected_sql, read="sqlite")
        target_tables = {table.name.casefold() for table in target.find_all(exp.Table)}
        for result, sql in zip(previous_results, previous_sql, strict=True):
            if _is_empty_evidence(result):
                continue
            probe = sqlglot.parse_one(sql, read="sqlite")
            probe_tables = {table.name.casefold() for table in probe.find_all(exp.Table)}
            if target_tables & probe_tables and (
                probe.find(exp.Group) is not None
                or probe.find(exp.Max) is not None
                or probe.find(exp.Min) is not None
            ):
                return True
    except Exception:
        # Validation failures here must only make a zero answer harder to claim.
        return False
    return False


def _json_safe(value: object) -> str:
    if isinstance(value, bytes):
        return "[valor binário omitido]"
    return str(value)
