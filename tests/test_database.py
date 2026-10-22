"""Behavioral checks for the SQLite tool boundary, using synthetic data only."""

import json
import os
import sqlite3
from contextlib import closing

import pytest

import analista_dados.database as database_module
from analista_dados.database import QueryError, SQLiteReadOnly


def make_db(tmp_path, ddl: str, rows: tuple[tuple, ...] = ()):
    path = tmp_path / "sample.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(ddl)
        if rows:
            connection.executemany("INSERT INTO eventos (id, canal) VALUES (?, ?)", rows)
    return path


def make_flag_db(tmp_path, values: tuple[object, ...], declared_type: str = "INTEGER"):
    path = tmp_path / "flags.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"CREATE TABLE eventos (resolvido {declared_type})")
        connection.executemany("INSERT INTO eventos VALUES (?)", ((value,) for value in values))
        connection.commit()
    return path


def test_discovers_schema_and_reads_aggregate(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")

    store = SQLiteReadOnly(path)

    assert store.inspect_schema()["tables"][0]["name"] == "eventos"
    result = store.run_sql("SELECT COUNT(*) AS total FROM eventos")
    assert result.columns == ("total",)
    assert result.rows == ((0,),)


def test_schema_exposes_exact_category_values_without_private_rows(tmp_path):
    path = tmp_path / "categories.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE compras (id INTEGER, canal TEXT, email TEXT)")
        connection.executemany(
            "INSERT INTO compras VALUES (?, ?, ?)",
            (
                (1, "App", "one@example.invalid"),
                (2, "App", "two@example.invalid"),
                (3, "Loja Física", "three@example.invalid"),
            ),
        )
        connection.execute("CREATE TABLE suporte (tipo_contato TEXT)")
        connection.execute("INSERT INTO suporte VALUES ('Reclamação')")

    schema = SQLiteReadOnly(path).inspect_schema()
    serialized = json.dumps(schema, ensure_ascii=False)

    assert '"App"' in serialized
    assert '"Reclamação"' in serialized
    assert '"app"' not in serialized
    assert "@example.invalid" not in serialized


def test_schema_hides_entire_category_when_value_looks_like_contact_data(tmp_path):
    path = tmp_path / "unsafe-category.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE compras (canal TEXT)")
        connection.executemany(
            "INSERT INTO compras VALUES (?)",
            [("App",), ("5511999999999",)],
        )

    schema = SQLiteReadOnly(path).inspect_schema()
    assert "categorical_values" not in schema["tables"][0]
    with pytest.raises(QueryError):
        SQLiteReadOnly(path).run_sql("SELECT canal, COUNT(*) FROM compras GROUP BY canal")


def test_schema_never_previews_primary_key_disguised_as_category(tmp_path):
    path = tmp_path / "category-key.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE compras (canal TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO compras VALUES ('SYNTHETIC PERSON NAME')")

    schema = SQLiteReadOnly(path).inspect_schema()
    assert "categorical_values" not in schema["tables"][0]
    assert "SYNTHETIC PERSON NAME" not in json.dumps(schema)


def test_schema_previews_only_explicitly_reviewed_table_column_pairs(tmp_path):
    path = tmp_path / "unknown-table.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE pessoas (canal TEXT)")
        connection.execute("INSERT INTO pessoas VALUES ('SYNTHETIC PERSON NAME')")

    schema = SQLiteReadOnly(path).inspect_schema()
    assert "categorical_values" not in schema["tables"][0]
    assert "SYNTHETIC PERSON NAME" not in json.dumps(schema)
    reviewed = SQLiteReadOnly(path, category_fields={("pessoas", "canal")}).inspect_schema()
    assert reviewed["tables"][0]["categorical_values"]["canal"] == [
        {"value": "SYNTHETIC PERSON NAME", "count": 1}
    ]


def test_schema_normalizes_free_form_declared_types(tmp_path):
    path = tmp_path / "types.db"
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE metricas (valor "TEXT SYNTHETIC PRIVATE SENTINEL")')

    schema = SQLiteReadOnly(path).inspect_schema()
    assert schema["tables"][0]["columns"][0]["type"] == "TEXT"
    assert "SYNTHETIC PRIVATE SENTINEL" not in json.dumps(schema)


def test_schema_does_not_materialize_unbounded_category_values(tmp_path):
    path = tmp_path / "huge-category.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE compras (canal TEXT)")
        connection.execute("INSERT INTO compras VALUES (?)", ("A" * 70_000,))

    with pytest.raises(QueryError, match="schema|esquema"):
        SQLiteReadOnly(path).inspect_schema()


def test_schema_rejects_oversized_metadata_before_model_payload(tmp_path):
    path = tmp_path / "wide-schema.db"
    with sqlite3.connect(path) as connection:
        columns = ", ".join(f"column_{number:02d} TEXT" for number in range(20))
        for index in range(160):
            connection.execute(f"CREATE TABLE table_{index:03d} ({columns})")

    with pytest.raises(QueryError, match="schema|esquema|128 KiB"):
        SQLiteReadOnly(path).inspect_schema()


def test_schema_inspection_reflects_database_changes_between_calls(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    store = SQLiteReadOnly(path)
    assert [table["name"] for table in store.inspect_schema()["tables"]] == ["eventos"]

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE campanhas (id INTEGER)")
        connection.commit()

    assert [table["name"] for table in store.inspect_schema()["tables"]] == [
        "campanhas",
        "eventos",
    ]


def test_schema_inspection_reflects_wal_new_category(tmp_path):
    path = tmp_path / "mutable.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE compras (canal TEXT)")
        writer.execute("INSERT INTO compras VALUES ('App')")
        writer.commit()
        store = SQLiteReadOnly(path)
        first = store.inspect_schema()["tables"][0]["categorical_values"]["canal"]
        writer.execute("INSERT INTO compras VALUES ('Site')")
        writer.commit()
        second = store.inspect_schema()["tables"][0]["categorical_values"]["canal"]

    assert first == [{"value": "App", "count": 1}]
    assert {item["value"] for item in second} == {"App", "Site"}


def test_run_sql_revalidates_replaced_database_with_same_size_and_mtime(tmp_path):
    path = tmp_path / "clientes.db"
    replacement = tmp_path / "replacement.db"
    for database_path, state in (
        (path, "Seguro"),
        (replacement, "person@example.invalid"),
    ):
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("CREATE TABLE clientes (estado TEXT)")
            connection.execute("INSERT INTO clientes VALUES (?)", (state,))
            connection.commit()

    store = SQLiteReadOnly(path)
    assert store.inspect_schema()["tables"][0]["categorical_values"]["estado"] == [
        {"value": "Seguro", "count": 1}
    ]
    original_stat = path.stat()
    assert replacement.stat().st_size == original_stat.st_size
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    os.replace(replacement, path)
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (
        original_stat.st_size,
        original_stat.st_mtime_ns,
    )
    with closing(sqlite3.connect(path)) as observer:
        assert observer.execute("SELECT estado FROM clientes").fetchone() == (
            "person@example.invalid",
        )

    with pytest.raises(QueryError):
        store.run_sql("SELECT estado, COUNT(*) FROM clientes GROUP BY estado")


def test_run_sql_uses_approved_wal_snapshot_after_concurrent_update(tmp_path, monkeypatch):
    path = tmp_path / "clientes-wal.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE clientes (estado TEXT)")
        writer.execute("INSERT INTO clientes VALUES ('Seguro')")
        writer.commit()

        original_validate_sql = database_module.validate_sql

        def update_after_validation(sql, schema):
            original_validate_sql(sql, schema)
            writer.execute("UPDATE clientes SET estado = ?", ("person@example.invalid",))
            writer.commit()

        monkeypatch.setattr(database_module, "validate_sql", update_after_validation)
        result = SQLiteReadOnly(path).run_sql(
            "SELECT estado, COUNT(*) FROM clientes GROUP BY estado"
        )
        assert writer.execute("SELECT estado FROM clientes").fetchone() == (
            "person@example.invalid",
        )

    assert result.rows == (("Seguro", 1),)


def test_schema_discovery_respects_timeout(tmp_path):
    path = make_db(
        tmp_path,
        "CREATE TABLE eventos (id INTEGER, canal TEXT);",
        tuple((index, "App") for index in range(20_000)),
    )

    with pytest.raises(QueryError, match="tempo"):
        SQLiteReadOnly(path, timeout_seconds=0.000000001).inspect_schema()


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
        with pytest.raises(QueryError, match="privada|protegida|Projeção|não permitida"):
            store.run_sql(sql)
    assert store.run_sql("SELECT COUNT(*) AS total FROM clientes").rows == ((1,),)


def test_interrupts_query_after_time_limit(tmp_path, monkeypatch):
    path = make_db(
        tmp_path,
        "CREATE TABLE eventos (id INTEGER, canal TEXT);",
        tuple((index, "chat") for index in range(1_000)),
    )
    store = SQLiteReadOnly(path, timeout_seconds=0.3)
    expensive_sql = (
        "SELECT COUNT(*) FROM eventos AS a CROSS JOIN eventos AS b CROSS JOIN eventos AS c"
    )
    query_started = False
    original_connect = store._connect

    class TrackedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *args):
            nonlocal query_started
            if sql == expensive_sql:
                query_started = True
            return self.connection.execute(sql, *args)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(store, "_connect", lambda: TrackedConnection(original_connect()))

    with pytest.raises(QueryError, match="tempo"):
        store.run_sql(expensive_sql)
    assert query_started


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
    store = SQLiteReadOnly(path, row_limit=2, category_fields={("eventos", "canal")})

    aggregate = "SELECT canal, COUNT(*) AS total FROM eventos GROUP BY canal ORDER BY canal"
    limited = store.run_sql(aggregate)
    exact = store.run_sql(aggregate + " LIMIT 2")

    assert limited.rows == (("chat", 1), ("email", 1))
    assert limited.truncated is True
    assert exact.rows == (("chat", 1), ("email", 1))
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
    store = SQLiteReadOnly(path, category_fields={("eventos", "canal")})

    result = store.run_sql(
        "WITH base AS (SELECT canal AS canal, COUNT(*) AS total "
        "FROM eventos GROUP BY canal) SELECT COUNT(*) FROM base"
    )
    assert result.rows == ((1,),)
    with pytest.raises(QueryError, match="não encontrada|SQL inválido"):
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


def test_rejects_unbounded_scalar_function_before_execution(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")

    with pytest.raises(QueryError):
        SQLiteReadOnly(path).run_sql("SELECT printf('%.*c', 100000, 65)")


def test_denies_personal_column_variants_and_raw_campaign_names(tmp_path):
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
        with pytest.raises(QueryError, match="privada|protegida"):
            store.run_sql(f'SELECT "{column}" FROM dados')
    with pytest.raises(QueryError, match="privada|projeção"):
        store.run_sql("SELECT nome_campanha FROM dados")
    assert store.run_sql("SELECT COUNT(*) FROM dados").rows == ((1,),)


def test_denies_raw_text_before_result_budget(tmp_path):
    path = tmp_path / "large.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE dados (id INTEGER, texto TEXT)")
        connection.executemany(
            "INSERT INTO dados (id, texto) VALUES (?, ?)",
            ((index, "x" * 50_000) for index in range(3)),
        )
    store = SQLiteReadOnly(path)

    with pytest.raises(QueryError, match="privada|projeção"):
        store.run_sql("SELECT texto FROM dados ORDER BY id")

    assert store.run_sql("SELECT COUNT(*) AS total FROM dados").rows == ((3,),)


def test_rejects_large_column_metadata_even_when_query_has_no_rows(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    first_alias = "a" * 65_525
    second_alias = "b" * 65_525

    with pytest.raises(QueryError, match="resultado.*128 KiB"):
        SQLiteReadOnly(path).run_sql(
            f'SELECT COUNT(*) AS "{first_alias}", COUNT(*) AS "{second_alias}" FROM eventos'
        )


def test_denies_binary_values_before_serialization(tmp_path):
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

    with pytest.raises(QueryError, match="privada|projeção"):
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


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM clientes",
        "SELECT documento AS estado FROM clientes",
        "SELECT id AS estado, COUNT(*) FROM clientes",
        "SELECT MAX(documento) FROM clientes",
        "SELECT GROUP_CONCAT(documento) FROM clientes",
        "SELECT estado, COUNT(*) FROM clientes GROUP BY id",
        "WITH x AS (SELECT documento AS estado FROM clientes) "
        "SELECT estado, COUNT(*) FROM x GROUP BY estado",
        "SELECT (SELECT documento FROM clientes LIMIT 1) AS estado, COUNT(*) FROM clientes",
        "SELECT sql FROM sqlite_master",
    ],
)
def test_never_returns_individual_or_internal_values_to_model(tmp_path, sql):
    path = tmp_path / "sensitive.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE clientes (id INTEGER, estado TEXT, documento TEXT)")
        connection.executemany(
            "INSERT INTO clientes VALUES (?, ?, ?)",
            ((7, "SP", "PRIVATE-SENTINEL-7"), (8, "SP", "PRIVATE-SENTINEL-8")),
        )

    with pytest.raises(QueryError):
        SQLiteReadOnly(path).run_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT canal, COUNT(*) FROM compras WHERE canal = 'app' GROUP BY canal",
        "SELECT COUNT(*) FROM compras WHERE canal = 'app'",
    ],
)
def test_rejects_category_literal_not_present_in_database(tmp_path, sql):
    path = tmp_path / "categories.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE compras (id INTEGER, canal TEXT)")
        connection.execute("INSERT INTO compras VALUES (1, 'App')")

    with pytest.raises(QueryError, match="(?i)valor categórico"):
        SQLiteReadOnly(path).run_sql(sql)


def test_schema_marks_only_binary_integer_flags_without_value_previews(tmp_path):
    path = tmp_path / "flag-schema.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE eventos (resolvido INTEGER, interagiu INTEGER, idade INTEGER)"
        )
        connection.executemany(
            "INSERT INTO eventos VALUES (?, ?, ?)",
            ((0, 1, 0), (1, 0, 1), (None, None, 1)),
        )
        connection.commit()

    table = SQLiteReadOnly(path).inspect_schema()["tables"][0]
    assert table.get("safe_flag_columns") == ["resolvido", "interagiu"]
    assert "categorical_values" not in table


def test_schema_marks_exact_boolean_flags_without_exposing_values(tmp_path):
    path = make_flag_db(tmp_path, (0, 1, None), "BOOLEAN")
    store = SQLiteReadOnly(path)

    table = store.inspect_schema()["tables"][0]
    assert table["columns"][0]["type"] == "NUMERIC"
    assert table["columns"][0].get("declared_type") == "BOOLEAN"
    assert table.get("safe_flag_columns") == ["resolvido"]
    assert "categorical_values" not in table
    assert store.run_sql(
        "SELECT resolvido, COUNT(*) FROM eventos GROUP BY resolvido ORDER BY resolvido"
    ).rows == ((None, 1), (0, 1), (1, 1))


def test_run_sql_groups_safe_flag_but_rejects_raw_projection(tmp_path):
    path = make_flag_db(tmp_path, (0, 0, 1, None))
    store = SQLiteReadOnly(path)

    result = store.run_sql(
        "SELECT resolvido, COUNT(*) AS total FROM eventos GROUP BY resolvido ORDER BY resolvido"
    )
    assert result.rows == ((None, 1), (0, 2), (1, 1))
    for sql in (
        "SELECT resolvido FROM eventos",
        "SELECT resolvido FROM eventos GROUP BY resolvido",
        "SELECT COUNT(*) FROM eventos ORDER BY resolvido",
    ):
        with pytest.raises(QueryError):
            store.run_sql(sql)


@pytest.mark.parametrize(
    ("invalid_value", "storage_type"),
    (
        (2, "integer"),
        (0.5, "real"),
        ("person@example.invalid", "text"),
        (b"\x00", "blob"),
    ),
)
def test_integer_flag_with_out_of_domain_storage_is_private(tmp_path, invalid_value, storage_type):
    path = make_flag_db(tmp_path, (0, 1, None, invalid_value))
    with closing(sqlite3.connect(path)) as observer:
        assert observer.execute(
            "SELECT typeof(resolvido) FROM eventos WHERE rowid = 4"
        ).fetchone() == (storage_type,)

    store = SQLiteReadOnly(path)
    table = store.inspect_schema()["tables"][0]
    assert "safe_flag_columns" not in table
    assert "person@example.invalid" not in json.dumps(table)
    for sql in (
        "SELECT resolvido, COUNT(*) FROM eventos GROUP BY resolvido",
        "SELECT COUNT(*) FROM eventos WHERE resolvido = 0",
        "SELECT COUNT(resolvido) FROM eventos",
    ):
        with pytest.raises(QueryError):
            store.run_sql(sql)


@pytest.mark.parametrize(
    ("declared_type", "values"),
    (
        ("TEXT", ("0", "1")),
        ("REAL", (0.0, 1.0)),
        ("NUMERIC", (0, 1)),
        ("BOOL", (0, 1)),
        ("BOOLEAN(1)", (0, 1)),
    ),
)
def test_unapproved_declared_flag_type_remains_private(tmp_path, declared_type, values):
    path = make_flag_db(tmp_path, values, declared_type)
    store = SQLiteReadOnly(path)
    assert "safe_flag_columns" not in store.inspect_schema()["tables"][0]
    if declared_type == "TEXT":
        with closing(sqlite3.connect(path)) as observer:
            assert observer.execute(
                "SELECT DISTINCT typeof(resolvido) FROM eventos"
            ).fetchall() == [("text",)]
    with pytest.raises(QueryError):
        store.run_sql("SELECT COUNT(*) FROM eventos WHERE resolvido = 0")


@pytest.mark.parametrize("invalid_value", (2, 0.5, "pessoa@example.invalid", b"\x00"))
def test_boolean_declared_flag_with_nonbinary_storage_is_private(tmp_path, invalid_value):
    path = make_flag_db(tmp_path, (0, 1, None, invalid_value), "BOOLEAN")
    store = SQLiteReadOnly(path)
    table = store.inspect_schema()["tables"][0]
    assert table["columns"][0].get("declared_type") == "BOOLEAN"
    assert "safe_flag_columns" not in table
    assert "pessoa@example.invalid" not in json.dumps(table)
    for sql in (
        "SELECT resolvido, COUNT(*) FROM eventos GROUP BY resolvido",
        "SELECT COUNT(*) FROM eventos WHERE resolvido = 0",
        "SELECT COUNT(resolvido) FROM eventos",
    ):
        with pytest.raises(QueryError):
            store.run_sql(sql)


def test_run_sql_uses_safe_flag_snapshot_across_wal_update(tmp_path, monkeypatch):
    path = tmp_path / "flag-wal.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE eventos (resolvido INTEGER)")
        writer.executemany("INSERT INTO eventos VALUES (?)", ((0,), (1,)))
        writer.commit()

        original_validate_sql = database_module.validate_sql

        def update_after_validation(sql, schema):
            original_validate_sql(sql, schema)
            writer.execute(
                "UPDATE eventos SET resolvido = ? WHERE resolvido = 0",
                ("person@example.invalid",),
            )
            writer.commit()

        monkeypatch.setattr(database_module, "validate_sql", update_after_validation)
        result = SQLiteReadOnly(path).run_sql(
            "SELECT resolvido, COUNT(*) FROM eventos GROUP BY resolvido ORDER BY resolvido"
        )
        assert writer.execute("SELECT resolvido FROM eventos ORDER BY rowid").fetchall() == [
            ("person@example.invalid",),
            (1,),
        ]

    assert result.rows == ((0, 1), (1, 1))
