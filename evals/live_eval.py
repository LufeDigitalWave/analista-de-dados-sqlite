"""Optional live semantic gate for the five Desafio 1 questions.

Run from the repository root with ``python -m evals.live_eval [1 ... 5]``.
It requires DATABASE_PATH and OPENAI_API_KEY and makes paid model calls; the
offline tests in tests/test_live_eval.py never do. Only the five independent
aggregate queries in reference_queries.sql define expected values. The model's
SQL is executed unchanged by production SQLiteReadOnly and retained exactly in
memory; console SQL is redacted to avoid leaking literals or personal data.

Exit codes: 0 = every selected case passed, 1 = mismatch or manual review,
3 = missing configuration or unusable reference data.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import sqlglot
from sqlglot import exp

from analista_dados.agent import DataAnalyst
from analista_dados.database import SQLiteReadOnly
from analista_dados.models import Answer, QueryResult
from analista_dados.visualization import prepare_visualization

QUESTIONS = (
    "Liste os 5 estados com maior número de clientes que compraram via app em maio.",
    "Quantos clientes interagiram com campanhas de WhatsApp em 2024?",
    "Quais categorias de produto tiveram o maior número de compras em média por cliente?",
    "Qual o número de reclamações não resolvidas por canal?",
    "Qual a tendência de reclamações por canal no último ano?",
)
REFERENCE_SQL = Path(__file__).with_name("reference_queries.sql")

_COUNT_DISTINCT_CLIENT = re.compile(
    r"\bCOUNT\s*\(\s*DISTINCT\s+(?:[A-Za-z_]\w*\.)?cliente_id\s*\)", re.IGNORECASE
)
_LABEL_NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[,.]\d+)?")
_SQL_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class CaseAssessment:
    status: str
    issues: tuple[str, ...]
    aggregate_match: bool


class RecordingSQLite(SQLiteReadOnly):
    """Record evidence identity while keeping SQLiteReadOnly execution intact."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.successful_queries: list[tuple[str, QueryResult]] = []

    def run_sql(self, sql: str) -> QueryResult:
        result = super().run_sql(sql)
        self.successful_queries.append((sql, result))
        return result

    def sql_for(self, answer: Answer) -> str | None:
        for sql, result in self.successful_queries:
            if result is answer.data:
                return sql
        return None


def load_reference_rows(
    db_path: Path, *, queries_path: Path = REFERENCE_SQL
) -> tuple[tuple[tuple, ...], ...]:
    """Execute the five independent oracles using a separate read-only connection."""
    source = queries_path.read_text(encoding="utf-8")
    statements = [part.strip() for part in source.split(";") if part.strip()]
    if len(statements) != len(QUESTIONS) or any(
        not re.match(r"^(?:SELECT|WITH)\b", _without_comments(sql).strip(), re.IGNORECASE)
        for sql in statements
    ):
        raise ValueError("O arquivo de referência deve conter cinco consultas SELECT/WITH.")

    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        return tuple(
            tuple(tuple(row) for row in connection.execute(statement).fetchall())
            for statement in statements
        )


def _without_comments(sql: str) -> str:
    return _SQL_COMMENTS.sub(" ", sql)


def safe_sql_for_log(sql: str) -> str:
    """Identify a query without printing model-supplied identifiers or literals."""
    leading = _without_comments(sql).lstrip().upper()
    if leading.startswith("SELECT"):
        kind = "SELECT"
    elif leading.startswith("WITH"):
        kind = "WITH"
    else:
        kind = "SQL"
    digest = hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{kind} sha256={digest}"


def _fold(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def _as_count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if type(value) is float and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _as_number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_pairs(rows: Sequence[tuple], *, integer: bool) -> list[tuple[str, float]] | None:
    parsed: list[tuple[str, float]] = []
    for row in rows:
        if len(row) != 2 or not isinstance(row[0], str):
            return None
        value = _as_count(row[1]) if integer else _as_number(row[1])
        if value is None:
            return None
        parsed.append((row[0], float(value)))
    if len({label for label, _ in parsed}) != len(parsed):
        return None
    return parsed


def _label_number_verdict(text: str, label: str, expected: float, labels: Sequence[str]) -> str:
    folded = _fold(text)
    folded_label = _fold(label)
    occurrences = list(re.finditer(rf"(?<!\w){re.escape(folded_label)}(?!\w)", folded))
    if not occurrences:
        return "missing"
    verdicts: list[str] = []
    tolerance = 0.005 if not expected.is_integer() else 0.000001
    for occurrence in occurrences:
        clause_start = (
            max(
                folded.rfind(";", 0, occurrence.start()),
                folded.rfind("\n", 0, occurrence.start()),
            )
            + 1
        )
        clause_end = min(
            [
                position
                for delimiter in (";", "\n")
                if (position := folded.find(delimiter, occurrence.end())) >= 0
            ]
            or [len(folded)]
        )
        clause = folded[clause_start:clause_end]
        nearby = folded[occurrence.end() : clause_end][:60]
        match = _LABEL_NUMBER.search(nearby)
        if match is None:
            verdicts.append("missing")
            continue
        has_other_label = any(
            re.search(rf"(?<!\w){re.escape(_fold(other))}(?!\w)", clause)
            for other in labels
            if _fold(other) != folded_label
        )
        if has_other_label and not re.match(r"\s*(?::|=|teve\b|tem\b|com\b)\s*\d", nearby):
            verdicts.append("ambiguous")
            continue
        number = float(match.group().replace(",", "."))
        if math.isclose(number, expected, rel_tol=0, abs_tol=tolerance):
            verdicts.append("ok")
        elif number in {2024, 2025}:
            # A nearby year does not prove that the narrative claimed this count.
            verdicts.append("ambiguous")
        else:
            verdicts.append("wrong")
    if "wrong" in verdicts:
        return "wrong"
    if "ambiguous" in verdicts:
        return "ambiguous"
    return "ok" if "ok" in verdicts else "missing"


def _text_integer_verdict(text: str, expected: int, *, exclude_years: bool) -> str:
    numbers = [
        int(number)
        for number in re.findall(r"(?<![\d,.])[+-]?\d+(?![\d,.])", _fold(text))
        if not exclude_years or number not in {"2024", "2025"}
    ]
    if expected in numbers:
        return "ambiguous" if any(number != expected for number in numbers) else "ok"
    return "wrong" if numbers else "missing"


def _text_checks(
    case: int, answer: Answer, expected: Sequence[tuple], failures: list[str], reviews: list[str]
) -> None:
    text = answer.text
    noun = "clientes?" if case in {1, 2} else "compras?" if case == 3 else r"reclamac\w*"
    folded_text = _fold(text)
    absence = re.search(
        rf"\b(?:nao houve|nao ha|nenhum(?:a)?|zero)\s+(?:\d+\s+)?{noun}\b",
        folded_text,
    )
    if absence is not None:
        clause = re.split(r"[;\n.]", folded_text[absence.end() :], maxsplit=1)[0]
        localized = case in {4, 5} and re.search(
            r"\b(?:em|no|na)\s+(?!periodo\b|total\b)\w+", clause
        )
        if localized:
            reviews.append("answer_text_review")
        else:
            failures.append("answer_text_mismatch")
    if case == 1:
        labels = tuple(label for label, _ in expected)
        for label, count in expected:
            verdict = _label_number_verdict(text, label, float(count), labels)
            if verdict == "wrong":
                failures.append("answer_text_mismatch")
            elif verdict == "ambiguous":
                reviews.append("answer_text_review")
        if re.search(r"\bnenhum(?:a)?\s+cliente", _fold(text)):
            failures.append("answer_text_mismatch")
    elif case == 2:
        verdict = _text_integer_verdict(text, int(expected[0][0]), exclude_years=True)
        if verdict == "wrong":
            failures.append("answer_text_mismatch")
        elif verdict != "ok" or "cliente" not in _fold(text):
            reviews.append("answer_text_review")
    elif case == 3:
        labels = tuple(label for label, _ in expected)
        for label, average in expected:
            verdict = _label_number_verdict(text, label, float(average), labels)
            if verdict == "wrong":
                failures.append("answer_text_mismatch")
            elif verdict == "ambiguous":
                reviews.append("answer_text_review")
        explanation = _fold(text + " " + answer.assumptions)
        if not (
            ("cliente" in explanation or "comprador" in explanation)
            and "categoria" in explanation
            and ("distint" in explanation or "que compr" in explanation)
        ):
            reviews.append("q3_denominator_review")
    elif case == 4:
        labels = tuple(label for label, _ in expected)
        for label, count in expected:
            verdict = _label_number_verdict(text, label, float(count), labels)
            if verdict == "wrong":
                failures.append("answer_text_mismatch")
            elif verdict != "ok":
                reviews.append("answer_text_review")
    else:
        total = sum(int(row[2]) for row in expected)
        total_claims = re.findall(
            r"\b(?:foram|houve|total(?:\s+de|\s*:)?)\s+(\d+)\s+reclam",
            _fold(text),
        )
        if any(int(claim) != total for claim in total_claims):
            failures.append("answer_text_mismatch")
        elif "reclam" not in _fold(text):
            reviews.append("answer_text_review")


def _q2_sql_verdict(sql: str) -> str:
    statement = _without_comments(sql)
    if _COUNT_DISTINCT_CLIENT.search(statement):
        return "ok" if len(re.findall(r"\bCOUNT\s*\(", statement, re.IGNORECASE)) == 1 else "review"
    if _has_joined_customer_pk_count(sql, "campanhas_marketing"):
        return "ok" if len(re.findall(r"\bCOUNT\s*\(", statement, re.IGNORECASE)) == 1 else "review"
    if re.search(r"\bCOUNT\s*\(\s*DISTINCT\b", statement, re.IGNORECASE):
        return "review"
    if re.search(
        r"\b(?:GROUP\s+BY|SELECT\s+DISTINCT)\b[^;]*\bcliente_id\b",
        statement,
        re.IGNORECASE | re.DOTALL,
    ):
        return "review"
    return "wrong"


def _q3_has_two_decimal_metric(sql: str) -> bool:
    if not _COUNT_DISTINCT_CLIENT.search(sql) or not re.search(
        r"\bCOUNT\s*\(\s*\*\s*\)", sql, re.IGNORECASE
    ):
        return False
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return False
    return any(
        isinstance(precision := node.args.get("decimals"), exp.Literal)
        and not precision.is_string
        and precision.this == "2"
        and node.find(exp.Div) is not None
        for node in tree.find_all(exp.Round)
    )


def _has_joined_customer_pk_count(sql: str, source_table: str) -> bool:
    """Recognize COUNT(DISTINCT clientes.id) through a source-to-customer join."""
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return False
    aliases = {
        table.alias_or_name.casefold(): table.name.casefold() for table in tree.find_all(exp.Table)
    }
    has_distinct_customer = any(
        isinstance(count.this, exp.Distinct)
        and len(count.this.expressions) == 1
        and isinstance(column := count.this.expressions[0], exp.Column)
        and column.name.casefold() == "id"
        and aliases.get(column.table.casefold()) == "clientes"
        for count in tree.find_all(exp.Count)
    )
    if not has_distinct_customer:
        return False
    for equality in tree.find_all(exp.EQ):
        if not isinstance(equality.left, exp.Column) or not isinstance(equality.right, exp.Column):
            continue
        sides = {
            (aliases.get(column.table.casefold()), column.name.casefold())
            for column in (equality.left, equality.right)
        }
        if sides == {("clientes", "id"), (source_table, "cliente_id")}:
            return True
    return False


def _q1_sql_has_may_2025_filter(sql: str) -> bool:
    statement = _without_comments(sql)
    if re.search(r"\bOR\b", statement, re.IGNORECASE):
        return False
    date_column = r"(?:[A-Za-z_]\w*\.)?data_compra"
    date_value = rf"(?:DATE\s*\(\s*{date_column}\s*\)|{date_column})"
    lower = re.search(rf"\b{date_value}\s*>=\s*'2025-05-01'", statement, re.IGNORECASE)
    upper = re.search(rf"\b{date_value}\s*<\s*'2025-06-01'", statement, re.IGNORECASE)
    between = re.search(
        rf"\bDATE\s*\(\s*{date_column}\s*\)\s+BETWEEN\s+"
        r"'2025-05-01'\s+AND\s+'2025-05-31'",
        statement,
        re.IGNORECASE,
    )
    whole_month = re.search(
        rf"\bSTRFTIME\s*\(\s*'%Y-%m'\s*,\s*{date_column}\s*\)\s*=\s*'2025-05'",
        statement,
        re.IGNORECASE,
    )
    year = re.search(
        rf"\bSTRFTIME\s*\(\s*'%Y'\s*,\s*{date_column}\s*\)\s*=\s*'2025'",
        statement,
        re.IGNORECASE,
    )
    month = re.search(
        rf"\bSTRFTIME\s*\(\s*'%m'\s*,\s*{date_column}\s*\)\s*=\s*'05'",
        statement,
        re.IGNORECASE,
    )
    return bool((lower and upper) or between or whole_month or (year and month))


def _q2_sql_has_filters(sql: str) -> bool:
    statement = _without_comments(sql)
    if re.search(r"\bOR\b", statement, re.IGNORECASE):
        return False
    channel = re.search(r"\bcanal\s*=\s*'WhatsApp'", statement, re.IGNORECASE)
    interaction = re.search(r"\binteragiu\s*=\s*1\b", statement, re.IGNORECASE)
    date_column = r"(?:[A-Za-z_]\w*\.)?data_envio"
    date_value = rf"(?:DATE\s*\(\s*{date_column}\s*\)|{date_column})"
    lower = re.search(rf"\b{date_value}\s*>=\s*'2024-01-01'", statement, re.IGNORECASE)
    upper = re.search(
        rf"\b{date_value}\s*(?:<\s*'2025-01-01'|<=\s*'2024-12-31')",
        statement,
        re.IGNORECASE,
    )
    year_part = re.search(
        rf"\bSTRFTIME\s*\(\s*'%Y'\s*,\s*{date_column}\s*\)\s*=\s*'2024'",
        statement,
        re.IGNORECASE,
    )
    between = re.search(
        rf"\b{date_value}\s+BETWEEN\s+'2024-01-01'\s+AND\s+'2024-12-31'",
        statement,
        re.IGNORECASE,
    )
    year = (lower and upper) or year_part or between
    return bool(channel and interaction and year)


def _q5_sql_is_relative(sql: str) -> bool:
    statement = _without_comments(sql)
    if re.search(r"'\d{4}-\d{2}-\d{2}'", statement):
        return False
    if _q5_sql_has_projected_relative_bounds(statement):
        return True
    maximum = re.search(
        r"\bMAX\s*\(\s*(?:DATE\s*\(\s*)?"
        r"(?:[A-Za-z_]\w*\.)?data_contato\s*\)?\s*\)\s+AS\s+([A-Za-z_]\w*)",
        statement,
        re.IGNORECASE,
    )
    if maximum is None or not re.search(r"\bFROM\s+suporte\b", statement, re.IGNORECASE):
        return False
    alias = re.escape(maximum.group(1))
    lower = (
        rf"DATE\s*\(\s*(?:[A-Za-z_]\w*\.)?{alias}\s*,\s*"
        r"'-(?:1\s+year|12\s+months)'\s*\)"
    )
    alias_reference = rf"(?:[A-Za-z_]\w*\.)?{alias}\b"
    upper = rf"(?:DATE\s*\(\s*{alias_reference}\s*\)|{alias_reference})"
    upper_exclusive = rf"DATE\s*\(\s*{alias_reference}\s*,\s*'\+1 day'\s*\)"
    contact_date = (
        r"(?:DATE\s*\(\s*(?:[A-Za-z_]\w*\.)?data_contato\s*\)"
        r"|(?:[A-Za-z_]\w*\.)?data_contato)"
    )
    between = rf"\b{contact_date}\s+BETWEEN\s+{lower}\s+AND\s+{upper}"
    bounds = re.search(rf"\b{contact_date}\s*>=\s*{lower}", statement, re.IGNORECASE) and (
        re.search(rf"\b{contact_date}\s*<=\s*{upper}", statement, re.IGNORECASE)
        or re.search(rf"\b{contact_date}\s*<\s*{upper_exclusive}", statement, re.IGNORECASE)
    )
    return bool(re.search(between, statement, re.IGNORECASE) or bounds)


def _q5_sql_has_projected_relative_bounds(statement: str) -> bool:
    date_column = r"(?:[A-Za-z_]\w*\.)?data_contato"
    maximum = re.search(
        rf"\bMAX\s*\(\s*DATE\s*\(\s*{date_column}\s*\)\s*\)\s+AS\s+([A-Za-z_]\w*)",
        statement,
        re.IGNORECASE,
    )
    lower = re.search(
        rf"\bDATE\s*\(\s*MAX\s*\(\s*DATE\s*\(\s*{date_column}\s*\)\s*\)\s*,\s*"
        r"'-(?:1\s+year|12\s+months)'\s*\)\s+AS\s+([A-Za-z_]\w*)",
        statement,
        re.IGNORECASE,
    )
    if (
        maximum is None
        or lower is None
        or not re.search(r"\bFROM\s+suporte\b", statement, re.IGNORECASE)
    ):
        return False
    start = rf"(?:[A-Za-z_]\w*\.)?{re.escape(lower.group(1))}"
    end = rf"(?:[A-Za-z_]\w*\.)?{re.escape(maximum.group(1))}"
    end_exclusive = rf"DATE\s*\(\s*{end}\s*,\s*'\+1 day'\s*\)"
    contact = rf"(?:DATE\s*\(\s*{date_column}\s*\)|{date_column})"
    between = rf"\b{contact}\s+BETWEEN\s+{start}\s+AND\s+{end}\b"
    separate = re.search(rf"\b{contact}\s*>=\s*{start}\b", statement, re.IGNORECASE) and (
        re.search(rf"\b{contact}\s*<=\s*{end}\b", statement, re.IGNORECASE)
        or re.search(rf"\b{contact}\s*<\s*{end_exclusive}", statement, re.IGNORECASE)
    )
    return bool(re.search(between, statement, re.IGNORECASE) or separate)


def _q5_sql_is_definitely_wrong(sql: str) -> bool:
    """Separate proven period mistakes from equivalent SQL requiring review."""
    statement = _without_comments(sql)
    contact_date = (
        r"(?:DATE\s*\(\s*(?:[A-Za-z_]\w*\.)?data_contato\s*\)"
        r"|(?:[A-Za-z_]\w*\.)?data_contato)"
    )
    shifted_lower = (
        rf"\b{contact_date}\s*(?:>=|>|BETWEEN)\s*DATE\s*\(\s*"
        r"(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*\s*,\s*'-(?:1\s+year|12\s+months)'\s*,\s*"
        r"'\+\s*\d+\s*day[s]?'\s*\)"
    )
    if re.search(shifted_lower, statement, re.IGNORECASE):
        return True
    strict_lower = (
        rf"\b{contact_date}\s*>\s*DATE\s*\(\s*"
        r"(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*\s*,\s*'-(?:1\s+year|12\s+months)'"
    )
    return re.search(strict_lower, statement, re.IGNORECASE) is not None


def _q5_anchor_has_unreviewed_filter(tree: exp.Expression) -> bool:
    """A filtered MAX(date(data_contato)) is not the source-wide time anchor."""
    for maximum in tree.find_all(exp.Max):
        if not any(
            column.name.casefold() == "data_contato" for column in maximum.find_all(exp.Column)
        ):
            continue
        select = maximum.find_ancestor(exp.Select)
        if select is None:
            return True
        if any(select.args.get(key) for key in ("where", "joins", "group", "having", "qualify")):
            return True
        source = select.args.get("from_")
        if source is not None and any(
            nested.args.get("where") or nested.args.get("joins") or nested.args.get("group")
            for nested in source.find_all(exp.Select)
        ):
            return True
    return False


def _q5_literal_period_from_probe_verdict(
    sql: str, history: Sequence[tuple[str, QueryResult]]
) -> str:
    """Verify that literal bounds were calculated from a prior unfiltered date probe."""
    statement = _without_comments(sql)
    if re.search(r"\bOR\b", statement, re.IGNORECASE):
        return "unknown"
    if not re.search(r"\bFROM\s+suporte\b", statement, re.IGNORECASE):
        return "unknown"
    date_column = r"(?:[A-Za-z_]\w*\.)?data_contato"
    intervals = re.findall(
        rf"\b{date_column}\s+BETWEEN\s+'(\d{{4}}-\d{{2}}-\d{{2}})'\s+AND\s+"
        r"'(\d{4}-\d{2}-\d{2})'",
        statement,
        re.IGNORECASE,
    )
    if len(intervals) != 1 or len(re.findall(r"'\d{4}-\d{2}-\d{2}'", statement)) != 2:
        return "unknown"
    start, end = intervals[0]
    probe_pattern = (
        rf"^\s*SELECT\s+MAX\s*\(\s*DATE\s*\(\s*{date_column}\s*\)\s*\)"
        r"\s+AS\s+[A-Za-z_]\w*\s+FROM\s+suporte\b"
    )
    for prior_sql, result in history:
        if prior_sql == sql:
            break
        prior_statement = _without_comments(prior_sql)
        if not re.search(probe_pattern, prior_statement, re.IGNORECASE) or re.search(
            r"\bWHERE\b", prior_statement, re.IGNORECASE
        ):
            continue
        if (
            len(result.rows) != 1
            or len(result.rows[0]) != 1
            or not isinstance(result.rows[0][0], str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", result.rows[0][0]) is None
        ):
            continue
        latest = result.rows[0][0]
        with closing(sqlite3.connect(":memory:")) as connection:
            expected_start = connection.execute("SELECT date(?, '-1 year')", (latest,)).fetchone()[
                0
            ]
        return "verified" if (start, end) == (expected_start, latest) else "wrong"
    return "unknown"


def assess_case(
    case: int,
    answer: Answer,
    reference: Sequence[tuple],
    selected_sql: str | None,
    *,
    history: Sequence[tuple[str, QueryResult]] = (),
) -> CaseAssessment:
    """Accept only proven canonical semantics; unknown equivalent layouts need review."""
    if case not in range(1, 6):
        raise ValueError("case deve ser de 1 a 5")
    if not reference:
        return CaseAssessment("FAIL", ("empty_reference",), False)
    failures: list[str] = []
    reviews: list[str] = []
    aggregate_match = False
    data = answer.data
    if data is None:
        failures.append("no_evidence")
    elif data.truncated:
        failures.append("truncated_evidence")
    else:
        rows = data.rows
        if case in {1, 3, 4}:
            parsed = _parse_pairs(rows, integer=case != 3)
            expected_pairs = _parse_pairs(reference, integer=case != 3)
            if parsed is None or expected_pairs is None:
                reviews.append("layout_review")
            elif case == 1:
                if parsed == expected_pairs:
                    aggregate_match = True
                elif len(parsed) == len(expected_pairs) and dict(parsed) == dict(expected_pairs):
                    reviews.append("order_review")
                elif (
                    len(parsed) == len(expected_pairs)
                    and all(
                        dict(parsed).get(label) == count
                        for label, count in expected_pairs
                        if count > expected_pairs[-1][1]
                    )
                    and all(
                        count == expected_pairs[-1][1]
                        for label, count in parsed
                        if label
                        not in {
                            expected_label
                            for expected_label, expected_count in expected_pairs
                            if expected_count > expected_pairs[-1][1]
                        }
                    )
                ):
                    reviews.append("q1_tie_review")
                else:
                    failures.append("aggregate_mismatch")
            elif case == 3:
                expected_map = dict(expected_pairs)
                actual_map = dict(parsed)
                if expected_map.keys() != actual_map.keys():
                    failures.append("aggregate_mismatch")
                elif all(
                    math.isclose(value, expected_map[label], rel_tol=0, abs_tol=0.00005)
                    for label, value in parsed
                ):
                    if [label for label, _ in parsed] == [label for label, _ in expected_pairs]:
                        aggregate_match = True
                    else:
                        reviews.append("order_review")
                elif all(
                    math.isclose(value, expected_map[label], rel_tol=0, abs_tol=0.005)
                    for label, value in parsed
                ):
                    if (
                        selected_sql is not None
                        and _q3_has_two_decimal_metric(selected_sql)
                        and all(
                            math.isclose(value, round(expected_map[label], 2), abs_tol=0.000001)
                            for label, value in parsed
                        )
                        and [label for label, _ in parsed] == [label for label, _ in expected_pairs]
                    ):
                        aggregate_match = True
                    else:
                        reviews.append("rounding_review")
                else:
                    failures.append("aggregate_mismatch")
            elif dict(parsed) == dict(expected_pairs):
                aggregate_match = True
            else:
                failures.append("aggregate_mismatch")
        elif case == 2:
            if len(rows) != 1 or len(rows[0]) != 1 or _as_count(rows[0][0]) is None:
                reviews.append("layout_review")
            elif _as_count(rows[0][0]) == _as_count(reference[0][0]):
                aggregate_match = True
            else:
                failures.append("aggregate_mismatch")
        else:
            parsed_series: dict[tuple[str, str], int] = {}
            valid = True
            for row in rows:
                if (
                    len(row) != 3
                    or not isinstance(row[0], str)
                    or re.fullmatch(r"\d{4}-\d{2}", row[0]) is None
                    or not isinstance(row[1], str)
                    or _as_count(row[2]) is None
                ):
                    valid = False
                    break
                key = (row[0], row[1])
                if key in parsed_series:
                    valid = False
                    break
                parsed_series[key] = _as_count(row[2])  # type: ignore[assignment]
            if not valid:
                reviews.append("layout_review")
            else:
                expected_series = {
                    (month, channel): int(count) for month, channel, count in reference
                }
                if parsed_series == expected_series:
                    aggregate_match = True
                    try:
                        view = prepare_visualization(data, answer.chart, "auto")
                    except Exception:
                        reviews.append("q5_chart_review")
                    else:
                        if (
                            view.kind != "line"
                            or set(view.y_columns) != {channel for _, channel in expected_series}
                            or view.x_column is None
                            or set(view.frame[view.x_column])
                            != {month for month, _ in expected_series}
                        ):
                            reviews.append("q5_chart_review")
                else:
                    failures.append("aggregate_mismatch")

    if selected_sql is None:
        failures.append("no_selected_sql")
    else:
        try:
            tree = sqlglot.parse_one(selected_sql, read="sqlite")
        except Exception:
            reviews.append("sql_parse_review")
        else:
            if tree.find(exp.Or) is not None:
                reviews.append("or_predicate_review")
            if case == 5 and _q5_anchor_has_unreviewed_filter(tree):
                reviews.append("q5_anchor_review")
        if case == 2:
            verdict = _q2_sql_verdict(selected_sql)
            if verdict == "wrong":
                failures.append("q2_not_distinct_clients")
            elif verdict == "review":
                reviews.append("q2_distinct_sql_review")
            if not _q2_sql_has_filters(selected_sql):
                reviews.append("q2_filters_review")
        elif case == 5:
            if _q5_sql_is_definitely_wrong(selected_sql):
                failures.append("q5_not_relative_period")
            elif not _q5_sql_is_relative(selected_sql):
                probe_verdict = _q5_literal_period_from_probe_verdict(selected_sql, history)
                if probe_verdict == "wrong":
                    failures.append("q5_not_relative_period")
                elif probe_verdict != "verified":
                    reviews.append("q5_period_sql_review")
        elif case == 1 and (
            not (
                _COUNT_DISTINCT_CLIENT.search(_without_comments(selected_sql))
                or _has_joined_customer_pk_count(selected_sql, "compras")
            )
            or "'App'" not in selected_sql
            or not _q1_sql_has_may_2025_filter(selected_sql)
        ):
            reviews.append("q1_sql_review")
        elif case == 3 and not _COUNT_DISTINCT_CLIENT.search(_without_comments(selected_sql)):
            reviews.append("q3_denominator_sql_review")
        elif case == 4 and not (
            re.search(r"tipo_contato\s*=\s*'Reclamação'", selected_sql, re.IGNORECASE)
            and re.search(r"resolvido\s*=\s*0\b", selected_sql, re.IGNORECASE)
        ):
            reviews.append("q4_filter_sql_review")

    _text_checks(case, answer, reference, failures, reviews)
    issues = tuple(dict.fromkeys(failures + reviews))
    return CaseAssessment(
        "FAIL" if failures else "REVIEW" if reviews else "PASS", issues, aggregate_match
    )


def _reference_summary(case: int, reference: Sequence[tuple]) -> str:
    if not reference:
        return "rows=0"
    if case == 2:
        return f"rows={len(reference)} total={reference[0][0]}"
    if case in {4, 5}:
        position = 1 if case == 4 else 2
        return f"rows={len(reference)} total={sum(int(row[position]) for row in reference)}"
    return f"rows={len(reference)}"


def run_evaluation(
    db_path: Path,
    references: Sequence[Sequence[tuple]],
    model_factory: Callable[[int], object],
    *,
    selected: Sequence[int] = (1, 2, 3, 4, 5),
    out: TextIO = sys.stdout,
) -> int:
    """Run real DataAnalyst/SQLiteReadOnly with an injectable offline model."""
    if len(references) != 5 or any(case not in range(1, 6) for case in selected):
        raise ValueError("São necessários cinco oráculos e casos entre 1 e 5.")
    failures = 0
    for case in selected:
        db = RecordingSQLite(db_path)
        try:
            answer = DataAnalyst(db, model_factory(case)).ask(QUESTIONS[case - 1])
            outcome = assess_case(
                case,
                answer,
                references[case - 1],
                db.sql_for(answer),
                history=tuple(db.successful_queries),
            )
            steps = answer.steps
            actual_rows = len(answer.data.rows) if answer.data is not None else 0
            aggregate_match = outcome.aggregate_match
        except Exception as exc:
            # Exception messages and final prose may contain model/tool data.
            outcome = CaseAssessment("FAIL", (f"execution_{type(exc).__name__}",), False)
            steps = getattr(exc, "steps", ())
            actual_rows = 0
            aggregate_match = False
        failures += outcome.status != "PASS"
        print(f"CASE {case} {outcome.status} {' '.join(outcome.issues)}", file=out)
        print(
            f"AGGREGATES reference=({_reference_summary(case, references[case - 1])}) "
            f"actual_rows={actual_rows} match={aggregate_match}",
            file=out,
        )
        for step in steps:
            if step.sql:
                print(f"SQL {safe_sql_for_log(step.sql)}", file=out)
        if outcome.issues and outcome.issues[0].startswith("execution_"):
            break
    return int(failures != 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", type=int, choices=range(1, 6))
    args = parser.parse_args(argv)
    raw_db_path = os.getenv("DATABASE_PATH")
    if not raw_db_path or not Path(raw_db_path).is_file():
        print("SKIP: DATABASE_PATH ausente ou arquivo indisponível")
        return 3
    if not os.getenv("OPENAI_API_KEY"):
        print("SKIP: OPENAI_API_KEY ausente")
        return 3
    db_path = Path(raw_db_path)
    try:
        references = load_reference_rows(db_path)
    except Exception as exc:
        print(f"REFERENCE_FAIL: {type(exc).__name__}")
        return 3

    # Deliberately imported only after preflight; tests never instantiate a provider.
    from openai import OpenAI

    from analista_dados.model import OpenAIModel

    client = OpenAI(timeout=30.0, max_retries=1)
    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1")
    return run_evaluation(
        db_path,
        references,
        lambda _case: OpenAIModel(client, model_name),
        selected=tuple(args.cases) or (1, 2, 3, 4, 5),
    )


if __name__ == "__main__":
    raise SystemExit(main())
