"""Behavioral checks for the SQLite tool boundary, using synthetic data only."""

import json
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


def test_schema_inspection_is_cached_until_database_file_changes(tmp_path, monkeypatch):
    path = make_db(tmp_path, "CREATE TABLE eventos (id INTEGER, canal TEXT);")
    store = SQLiteReadOnly(path)
    connect_count = 0
    original_connect = store._connect

    def track_connect():
        nonlocal connect_count
        connect_count += 1
        return original_connect()

    monkeypatch.setattr(store, "_connect", track_connect)
    assert store.inspect_schema() == store.inspect_schema()
    assert connect_count == 1

    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE campanhas (id INTEGER)")

    assert [table["name"] for table in store.inspect_schema()["tables"]] == [
        "campanhas",
        "eventos",
    ]
    assert connect_count == 2


def test_schema_cache_refreshes_when_wal_receives_new_category(tmp_path):
    path = tmp_path / "mutable.db"
    with sqlite3.connect(path) as writer:
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


def test_interrupts_query_after_time_limit(tmp_path):
    path = make_db(
        tmp_path,
        "CREATE TABLE eventos (id INTEGER, canal TEXT);",
        tuple((index, "chat") for index in range(1_000)),
    )
    store = SQLiteReadOnly(path, timeout_seconds=0.0001)

    with pytest.raises(QueryError, match="tempo"):
        store.run_sql(
            "SELECT COUNT(*) FROM eventos AS a CROSS JOIN eventos AS b CROSS JOIN eventos AS c"
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
