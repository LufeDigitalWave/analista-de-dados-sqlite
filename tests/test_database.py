"""Behavioral checks for the SQLite tool boundary, using synthetic data only."""

import sqlite3

import pytest

from analista_dados.database import QueryError, SQLiteReadOnly


def make_db(tmp_path, ddl: str, rows: tuple[tuple, ...] = ()):
    path = tmp_path / "sample.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(ddl)
        if rows:
            connection.executemany("INSERT INTO eventos (id, canal) VALUES (?, ?)", rows)
    return path


def test_discovers_schema_and_reads_aggregate(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")

    store = SQLiteReadOnly(path)

    assert store.inspect_schema()["tables"][0]["name"] == "eventos"
    result = store.run_sql("SELECT COUNT(*) AS total FROM eventos")
    assert result.columns == ("total",)
    assert result.rows == ((0,),)


def test_denies_direct_personal_column_but_allows_aggregate(tmp_path):
    path = tmp_path / "private.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE clientes (id INTEGER, email TEXT)")
        connection.execute(
            "INSERT INTO clientes (id, email) VALUES (?, ?)",
            (1, "synthetic@example.invalid"),
        )

    store = SQLiteReadOnly(path)

    for sql in (
        "SELECT email FROM clientes",
        "SELECT * FROM clientes",
        "SELECT LOWER(email) FROM clientes",
    ):
        with pytest.raises(QueryError, match="protegida"):
            store.run_sql(sql)
    assert store.run_sql("SELECT COUNT(*) AS total FROM clientes").rows == ((1,),)


def test_interrupts_query_after_time_limit(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    store = SQLiteReadOnly(path, timeout_seconds=0.0001)

    with pytest.raises(QueryError, match="tempo"):
        store.run_sql(
            "WITH RECURSIVE seq(n) AS ("
            "VALUES(0) UNION ALL SELECT n + 1 FROM seq WHERE n < 500000"
            ") SELECT SUM(n) FROM seq"
        )


def test_rejects_out_of_range_limits(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")

    with pytest.raises(ValueError, match="row_limit"):
        SQLiteReadOnly(path, row_limit=0)
    with pytest.raises(ValueError, match="row_limit"):
        SQLiteReadOnly(path, row_limit=201)
    with pytest.raises(ValueError, match="timeout_seconds"):
        SQLiteReadOnly(path, timeout_seconds=0)


def test_returns_only_row_limit_and_marks_truncation(tmp_path):
    path = make_db(
        tmp_path,
        "CREATE TABLE eventos (id INTEGER, canal TEXT);",
        ((1, "chat"), (2, "email"), (3, "telefone")),
    )
    store = SQLiteReadOnly(path, row_limit=2)

    limited = store.run_sql("SELECT id FROM eventos ORDER BY id")
    exact = store.run_sql("SELECT id FROM eventos WHERE id < 3 ORDER BY id")

    assert limited.rows == ((1,), (2,))
    assert limited.truncated is True
    assert exact.rows == ((1,), (2,))
    assert exact.truncated is False


def test_discovers_new_table_and_foreign_key_without_hardcoded_schema(tmp_path):
    path = tmp_path / "relations.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE clientes (id INTEGER PRIMARY KEY)")
    store = SQLiteReadOnly(path)
    assert [table["name"] for table in store.inspect_schema()["tables"]] == ["clientes"]

    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE compras (id INTEGER, cliente_id INTEGER, "
            "FOREIGN KEY(cliente_id) REFERENCES clientes(id))"
        )

    schema = store.inspect_schema()["tables"]
    assert [table["name"] for table in schema] == ["clientes", "compras"]
    assert schema[0]["columns"][0] == {
        "name": "id",
        "type": "INTEGER",
        "nullable": False,
        "primary_key": True,
    }
    assert schema[1]["foreign_keys"] == [{"table": "clientes", "from": "cliente_id", "to": "id"}]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM eventos",
        "UPDATE eventos SET canal = 'x'",
        "WITH alvo AS (SELECT id FROM eventos) "
        "DELETE FROM eventos WHERE id IN (SELECT id FROM alvo)",
        "PRAGMA table_info(eventos)",
        "SELECT 1; DELETE FROM eventos",
    ],
)
def test_rejects_mutating_or_multiple_statements(tmp_path, sql):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);", ((1, "chat"),))
    store = SQLiteReadOnly(path)

    with pytest.raises(QueryError):
        store.run_sql(sql)

    assert store.run_sql("SELECT COUNT(*) AS total FROM eventos").rows == ((1,),)


def test_cte_and_sql_errors_are_returned_in_controlled_form(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);", ((1, "chat"),))
    store = SQLiteReadOnly(path)

    result = store.run_sql("WITH base AS (SELECT id FROM eventos) SELECT COUNT(*) FROM base")
    assert result.rows == ((1,),)
    with pytest.raises(QueryError, match="SQL inválido"):
        store.run_sql("SELECT missing_column FROM eventos")


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(QueryError, match="não encontrado"):
        SQLiteReadOnly(path).inspect_schema()

    assert not path.exists()


def test_corrupt_database_has_controlled_schema_error(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a SQLite database")

    with pytest.raises(QueryError, match="schema"):
        SQLiteReadOnly(path).inspect_schema()


def test_caps_oversized_scalar_result(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")

    result = SQLiteReadOnly(path).run_sql("SELECT printf('%.*c', 100000, 65)")

    # SQLite may return NULL or a bounded string, depending on its build.
    scalar = result.rows[0][0]
    assert scalar is None or len(scalar.encode("utf-8")) <= 64 * 1024


def test_denies_personal_column_variants_without_blocking_campaign_name(tmp_path):
    path = tmp_path / "variants.db"
    personal_columns = (
        "nome_cliente",
        "cliente_nome",
        "endereco",
        "telefone_contato",
        "cpf_cliente",
        "email_pessoal",
        "nome_completo",
        "CPFCliente",
        "EMAILCliente",
        "NOMECliente",
    )
    with sqlite3.connect(path) as connection:
        definitions = ", ".join(f'"{column}" TEXT' for column in personal_columns)
        connection.execute(f"CREATE TABLE dados ({definitions}, nome_campanha TEXT)")
        placeholders = ", ".join("?" for _ in range(len(personal_columns) + 1))
        connection.execute(
            f"INSERT INTO dados VALUES ({placeholders})",
            (*(["sintetico"] * len(personal_columns)), "Campanha de exemplo"),
        )

    store = SQLiteReadOnly(path)
    for column in personal_columns:
        with pytest.raises(QueryError, match="protegida"):
            store.run_sql(f'SELECT "{column}" FROM dados')
    assert store.run_sql("SELECT nome_campanha FROM dados").rows == (("Campanha de exemplo",),)


def test_rejects_result_when_total_payload_exceeds_budget(tmp_path):
    path = tmp_path / "large.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE dados (id INTEGER, texto TEXT)")
        connection.executemany(
            "INSERT INTO dados (id, texto) VALUES (?, ?)",
            ((index, "x" * 50_000) for index in range(3)),
        )
    store = SQLiteReadOnly(path)

    with pytest.raises(QueryError, match="resultado.*128 KiB"):
        store.run_sql("SELECT texto FROM dados ORDER BY id")

    assert store.run_sql("SELECT COUNT(*) AS total FROM dados").rows == ((3,),)


def test_rejects_large_column_metadata_even_when_query_has_no_rows(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    first_alias = "a" * 65_525
    second_alias = "b" * 65_525

    with pytest.raises(QueryError, match="resultado.*128 KiB"):
        SQLiteReadOnly(path).run_sql(f'SELECT 1 AS "{first_alias}", 2 AS "{second_alias}" WHERE 0')


def test_payload_budget_counts_binary_placeholder_sent_to_agent(tmp_path):
    path = tmp_path / "binary.db"
    blob_columns = ", ".join(f"blob_{index} BLOB" for index in range(10))
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE dados (id INTEGER, texto TEXT, {blob_columns})")
        placeholders = ", ".join("?" for _ in range(12))
        connection.executemany(
            f"INSERT INTO dados VALUES ({placeholders})",
            ((index, "x" * 450, *([b""] * 10)) for index in range(200)),
        )
    selected_blobs = ", ".join(f"blob_{index}" for index in range(10))

    with pytest.raises(QueryError, match="resultado.*128 KiB"):
        SQLiteReadOnly(path).run_sql(f"SELECT texto, {selected_blobs} FROM dados ORDER BY id")


def test_allows_state_and_customer_id_in_distinct_join_aggregate(tmp_path):
    path = tmp_path / "aggregation.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE clientes (id INTEGER, estado TEXT)")
        connection.execute("CREATE TABLE compras (id INTEGER, cliente_id INTEGER)")
        connection.executemany(
            "INSERT INTO clientes (id, estado) VALUES (?, ?)",
            ((1, "SP"), (2, "SP"), (3, "RJ")),
        )
        connection.executemany(
            "INSERT INTO compras (id, cliente_id) VALUES (?, ?)",
            ((10, 1), (11, 1), (12, 2), (13, 3)),
        )

    result = SQLiteReadOnly(path).run_sql(
        "SELECT c.estado, COUNT(DISTINCT p.cliente_id) AS clientes_unicos "
        "FROM compras AS p JOIN clientes AS c ON c.id = p.cliente_id "
        "GROUP BY c.estado ORDER BY c.estado"
    )

    assert result.columns == ("estado", "clientes_unicos")
    assert result.rows == (("RJ", 1), ("SP", 2))
