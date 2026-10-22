"""Read-only SQLite boundary for model-proposed analytical queries."""

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from time import perf_counter

from .models import QueryResult
from .sql_policy import FLAG_NAMES, SQLPolicyError, validate_sql

PROTECTED_TOKENS = frozenset({"email", "cpf", "telefone", "endereco", "celular"})
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "randomblob", "zeroblob"})
MAX_VALUE_BYTES = 64 * 1024
MAX_RESULT_BYTES = 128 * 1024
MAX_CATEGORY_VALUES = 30
DEFAULT_CATEGORY_FIELDS = frozenset(
    {
        ("clientes", "estado"),
        ("compras", "canal"),
        ("compras", "categoria"),
        ("campanhas_marketing", "canal"),
        ("suporte", "canal"),
        ("suporte", "tipo_contato"),
    }
)


def _sqlite_affinity(declared_type: str) -> str:
    """Expose SQLite affinity, not the free-form declared type, to the model."""
    upper = declared_type.upper()
    if "INT" in upper:
        return "INTEGER"
    if any(token in upper for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in upper or not upper:
        return "BLOB"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _is_protected_column(column: str) -> bool:
    # Split camelCase and acronym boundaries, then fold accents for common PT-BR names.
    separated = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", column)
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", separated.casefold())
        if not unicodedata.combining(character)
    )
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", folded)))
    if tokens & PROTECTED_TOKENS:
        return True
    return "nome" in tokens and tokens != {"nome", "campanha"}


def _tool_output_size(columns: tuple[str, ...], rows: list[tuple]) -> int:
    # This envelope mirrors the SQL tool result. ASCII escaping is a conservative
    # upper bound for the UTF-8 JSON later sent by the agent.
    payload = {
        "ok": True,
        "query_number": 999,
        "columns": columns,
        "rows": rows,
        "truncated": False,
        "elapsed_ms": 999999999999.99,
    }
    return len(
        json.dumps(
            payload,
            ensure_ascii=True,
            default=lambda value: (
                "[valor binário omitido]" if isinstance(value, bytes) else str(value)
            ),
        )
    )


def _schema_output_size(schema: dict) -> int:
    return len(json.dumps({"ok": True, "schema": schema}, ensure_ascii=True))


class QueryError(ValueError):
    """A proposed query was rejected or could not be executed safely."""


class SQLiteReadOnly:
    def __init__(
        self,
        path: Path,
        row_limit: int = 200,
        timeout_seconds: float = 3.0,
        category_fields: Iterable[tuple[str, str]] | None = None,
    ):
        if not 1 <= row_limit <= 200:
            raise ValueError("row_limit deve estar entre 1 e 200.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds deve ser positivo.")
        self.path = Path(path)
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds
        approved = DEFAULT_CATEGORY_FIELDS if category_fields is None else category_fields
        self.category_fields = frozenset(
            (table.casefold(), column.casefold()) for table, column in approved
        )

    def inspect_schema(self) -> dict:
        deadline = perf_counter() + self.timeout_seconds
        with closing(self._connect()) as connection:
            return self._inspect_schema(connection, deadline)

    def _inspect_schema(self, connection: sqlite3.Connection, deadline: float) -> dict:
        """Read policy metadata from the same transaction later used by SQL."""
        time_expired = False

        def check_deadline() -> int:
            nonlocal time_expired
            time_expired = perf_counter() >= deadline
            return int(time_expired)

        try:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
            connection.set_progress_handler(check_deadline, 1000)
            if check_deadline():
                raise QueryError("Leitura do schema interrompida por limite de tempo.")
            connection.execute("BEGIN")
            names = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            schema: dict = {"tables": []}
            for (name,) in names:
                quoted = '"' + name.replace('"', '""') + '"'
                column_rows = list(connection.execute(f"PRAGMA table_info({quoted})"))
                columns = [
                    {
                        "name": row[1],
                        "type": _sqlite_affinity(row[2]),
                        "nullable": not bool(row[3] or row[5]),
                        "primary_key": bool(row[5]),
                        **(
                            {"declared_type": "BOOLEAN"}
                            if row[1].casefold() in FLAG_NAMES and row[2].upper() == "BOOLEAN"
                            else {}
                        ),
                    }
                    for row in column_rows
                ]
                foreign_keys = [
                    {"table": row[2], "from": row[3], "to": row[4]}
                    for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
                ]
                table: dict = {
                    "name": name,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                }
                categorical_values = {}
                for column in columns:
                    category_name = column["name"]
                    if (
                        (name.casefold(), category_name.casefold()) not in self.category_fields
                        or column["primary_key"]
                        or category_name.casefold().endswith("_id")
                        or _is_protected_column(category_name)
                        or column["type"] != "TEXT"
                    ):
                        continue
                    quoted_column = '"' + category_name.replace('"', '""') + '"'
                    categories = connection.execute(
                        f"SELECT {quoted_column}, COUNT(*) FROM {quoted} "
                        f"WHERE {quoted_column} IS NOT NULL GROUP BY {quoted_column} "
                        f"ORDER BY COUNT(*) DESC, {quoted_column} LIMIT ?",
                        (MAX_CATEGORY_VALUES + 1,),
                    ).fetchall()
                    if len(categories) <= MAX_CATEGORY_VALUES and all(
                        isinstance(value, str)
                        and len(value) <= 80
                        and value.isprintable()
                        and "@" not in value
                        and not any(character.isdigit() for character in value)
                        and "://" not in value
                        and "www." not in value.casefold()
                        for value, _ in categories
                    ):
                        categorical_values[category_name] = [
                            {"value": value, "count": count} for value, count in categories
                        ]
                if categorical_values:
                    table["categorical_values"] = categorical_values
                safe_flag_columns = []
                for column in columns:
                    flag_name = column["name"]
                    if (
                        flag_name.casefold() not in FLAG_NAMES
                        or not (
                            column["type"] == "INTEGER" or column.get("declared_type") == "BOOLEAN"
                        )
                        or column["primary_key"]
                    ):
                        continue
                    quoted_flag = '"' + flag_name.replace('"', '""') + '"'
                    invalid_flag = connection.execute(
                        f"SELECT 1 FROM {quoted} WHERE {quoted_flag} IS NOT NULL "
                        f"AND (typeof({quoted_flag}) != 'integer' "
                        f"OR {quoted_flag} NOT IN (0, 1)) LIMIT 1"
                    ).fetchone()
                    if invalid_flag is None:
                        safe_flag_columns.append(flag_name)
                if safe_flag_columns:
                    table["safe_flag_columns"] = safe_flag_columns
                schema["tables"].append(table)
                if _schema_output_size(schema) > MAX_RESULT_BYTES:
                    raise QueryError(
                        "O schema excede o limite total de 128 KiB; use um banco menor."
                    )
                if check_deadline():
                    raise QueryError("Leitura do schema interrompida por limite de tempo.")
        except sqlite3.Error as exc:
            if time_expired:
                raise QueryError("Leitura do schema interrompida por limite de tempo.") from exc
            raise QueryError("Não foi possível ler o schema do banco SQLite.") from exc
        return schema

    def run_sql(self, sql: str) -> QueryResult:
        statement = sql.strip()
        if not re.match(r"^(?:SELECT|WITH)\b", statement, re.IGNORECASE):
            raise QueryError("Apenas consultas SELECT/WITH são permitidas.")
        started = perf_counter()
        deadline = started + self.timeout_seconds
        blocked: list[str] = []
        time_expired = False

        def authorize(action: int, name: str | None, detail: str | None, *_: str | None) -> int:
            if action == sqlite3.SQLITE_READ and detail and _is_protected_column(detail):
                blocked.append("Leitura de coluna protegida não permitida.")
                return sqlite3.SQLITE_DENY
            if (
                action == sqlite3.SQLITE_FUNCTION
                and detail
                and detail.casefold() in DENIED_FUNCTIONS
            ):
                blocked.append("Função SQL não permitida.")
                return sqlite3.SQLITE_DENY
            if action not in ALLOWED_ACTIONS:
                blocked.append("Operação SQL não permitida.")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def check_deadline() -> int:
            nonlocal time_expired
            time_expired = perf_counter() >= deadline
            return int(time_expired)

        try:
            with closing(self._connect()) as connection:
                schema = self._inspect_schema(connection, deadline)
                try:
                    validate_sql(statement, schema)
                except SQLPolicyError as exc:
                    raise QueryError(str(exc)) from exc
                connection.set_authorizer(authorize)
                connection.set_progress_handler(check_deadline, 1000)
                if check_deadline():
                    raise QueryError("Consulta interrompida por limite de tempo.")
                cursor = connection.execute(statement)
                columns = tuple(column[0] for column in cursor.description)
                if _tool_output_size(columns, []) > MAX_RESULT_BYTES:
                    raise QueryError(
                        "O resultado SQL excede o limite total de 128 KiB; agregue os dados."
                    )
                rows: list[tuple] = []
                truncated = False
                for row in cursor:
                    if len(rows) == self.row_limit:
                        truncated = True
                        break
                    rows.append(tuple(row))
                    if _tool_output_size(columns, rows) > MAX_RESULT_BYTES:
                        raise QueryError(
                            "O resultado SQL excede o limite total de 128 KiB; agregue os dados."
                        )
                return QueryResult(
                    columns=columns,
                    rows=tuple(rows),
                    truncated=truncated,
                    elapsed_ms=round((perf_counter() - started) * 1000, 2),
                )
        except sqlite3.Error as exc:
            if time_expired:
                message = "Consulta interrompida por limite de tempo."
            else:
                message = blocked[0] if blocked else f"SQL inválido ou não permitido: {exc}"
            raise QueryError(message) from exc

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise QueryError("Banco SQLite não encontrado.")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=self.timeout_seconds)
            connection.execute("PRAGMA query_only = ON")
            return connection
        except sqlite3.Error as exc:
            raise QueryError("Não foi possível abrir o banco SQLite.") from exc
