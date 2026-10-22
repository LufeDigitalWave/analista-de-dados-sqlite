"""Read-only SQLite boundary for model-proposed analytical queries."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from time import perf_counter

from .models import QueryResult

PROTECTED_COLUMNS = frozenset({"nome", "email", "cpf", "telefone"})
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "randomblob", "zeroblob"})
MAX_VALUE_BYTES = 64 * 1024


class QueryError(ValueError):
    """A proposed query was rejected or could not be executed safely."""


class SQLiteReadOnly:
    def __init__(self, path: Path, row_limit: int = 200, timeout_seconds: float = 3.0):
        if not 1 <= row_limit <= 200:
            raise ValueError("row_limit deve estar entre 1 e 200.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds deve ser positivo.")
        self.path = Path(path)
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds

    def inspect_schema(self) -> dict:
        try:
            with closing(self._connect()) as connection:
                names = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                tables = []
                for (name,) in names:
                    quoted = '"' + name.replace('"', '""') + '"'
                    columns = [
                        {
                            "name": row[1],
                            "type": row[2],
                            "nullable": not bool(row[3] or row[5]),
                            "primary_key": bool(row[5]),
                        }
                        for row in connection.execute(f"PRAGMA table_info({quoted})")
                    ]
                    foreign_keys = [
                        {"table": row[2], "from": row[3], "to": row[4]}
                        for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
                    ]
                    tables.append({"name": name, "columns": columns, "foreign_keys": foreign_keys})
        except sqlite3.Error as exc:
            raise QueryError("Não foi possível ler o schema do banco SQLite.") from exc
        return {"tables": tables}

    def run_sql(self, sql: str) -> QueryResult:
        statement = sql.strip()
        if not re.match(r"^(?:SELECT|WITH)\b", statement, re.IGNORECASE):
            raise QueryError("Apenas consultas SELECT/WITH são permitidas.")
        started = perf_counter()
        deadline = started + self.timeout_seconds
        blocked: list[str] = []
        time_expired = False

        def authorize(action: int, name: str | None, detail: str | None, *_: str | None) -> int:
            if action == sqlite3.SQLITE_READ and detail and detail.casefold() in PROTECTED_COLUMNS:
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
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
                connection.set_authorizer(authorize)
                connection.set_progress_handler(check_deadline, 1000)
                cursor = connection.execute(statement)
                rows = tuple(tuple(row) for row in cursor.fetchmany(self.row_limit + 1))
                return QueryResult(
                    columns=tuple(column[0] for column in cursor.description),
                    rows=rows[: self.row_limit],
                    truncated=len(rows) > self.row_limit,
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
