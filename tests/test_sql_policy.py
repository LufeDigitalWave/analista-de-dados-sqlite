"""Privacy policy checks use schema metadata and synthetic values only."""

import sqlite3
from importlib import import_module

import pytest


def _column(name: str, kind: str = "TEXT", *, primary_key: bool = False) -> dict:
    return {"name": name, "type": kind, "primary_key": primary_key}


SCHEMA = {
    "tables": [
        {
            "name": "clientes",
            "columns": [
                _column("id", "INTEGER", primary_key=True),
                _column("estado"),
                _column("email"),
                _column("nome"),
                _column("idade", "INTEGER"),
                _column("cidade"),
                _column("profissao"),
                _column("genero"),
                _column("documento"),
            ],
            "categorical_values": {
                "estado": [{"value": "São Paulo", "count": 8}],
            },
        },
        {
            "name": "compras",
            "columns": [
                _column("id", "INTEGER", primary_key=True),
                _column("cliente_id", "INTEGER"),
                _column("canal"),
                _column("categoria"),
                _column("data_compra"),
                _column("valor", "REAL"),
                _column("segredo"),
            ],
            "categorical_values": {
                "canal": [
                    {"value": "App", "count": 10},
                    {"value": "Loja Física", "count": 5},
                ],
                "categoria": [{"value": "Roupas", "count": 5}],
            },
        },
        {
            "name": "campanhas_marketing",
            "columns": [
                _column("cliente_id", "INTEGER"),
                _column("canal"),
                _column("interagiu", "INTEGER"),
                _column("data_envio"),
            ],
            "categorical_values": {
                "canal": [{"value": "WhatsApp", "count": 20}],
            },
        },
        {
            "name": "suporte",
            "columns": [
                _column("canal"),
                _column("tipo_contato"),
                _column("resolvido", "INTEGER"),
                _column("data_contato"),
                _column("telefone"),
            ],
            "categorical_values": {
                "canal": [
                    {"value": "Telefone", "count": 5},
                    {"value": "Chat", "count": 5},
                    {"value": "E-mail", "count": 5},
                ],
                "tipo_contato": [{"value": "Reclamação", "count": 10}],
            },
        },
    ]
}


def _policy():
    """Make missing policy a test failure during the first RED cycle."""
    try:
        return import_module("analista_dados.sql_policy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Módulo de política SQL ainda ausente: {exc}")


def test_date_literal_rejection_explains_safe_relative_window() -> None:
    policy = _policy()
    sql = "SELECT COUNT(*) FROM suporte WHERE date(data_contato) >= date('2025-07-22', '-1 year')"
    with pytest.raises(policy.SQLPolicyError, match=r"MAX\(date\(coluna_data\)\)"):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        """SELECT c.estado, COUNT(DISTINCT p.cliente_id) AS clientes
        FROM compras AS p JOIN clientes AS c ON c.id = p.cliente_id
        WHERE p.canal = 'App' AND p.data_compra >= '2025-05-01'
          AND p.data_compra < '2025-06-01'
        GROUP BY c.estado ORDER BY clientes DESC, c.estado ASC LIMIT 5""",
        """SELECT COUNT(DISTINCT cliente_id) AS clientes
        FROM campanhas_marketing
        WHERE canal = 'WhatsApp' AND interagiu = 1
          AND data_envio >= '2024-01-01' AND data_envio < '2025-01-01'""",
        """SELECT categoria,
          ROUND(CAST(COUNT(*) AS REAL) / COUNT(DISTINCT cliente_id), 4)
          AS compras_por_comprador
        FROM compras GROUP BY categoria ORDER BY compras_por_comprador DESC""",
        """SELECT canal, COUNT(*) AS reclamacoes FROM suporte
        WHERE tipo_contato = 'Reclamação' AND resolvido = 0
        GROUP BY canal ORDER BY reclamacoes DESC""",
        """WITH limite AS (SELECT MAX(date(data_contato)) AS fim FROM suporte)
        SELECT strftime('%Y-%m', s.data_contato) AS mes,
               s.canal, COUNT(*) AS reclamacoes
        FROM suporte AS s CROSS JOIN limite AS l
        WHERE s.tipo_contato = 'Reclamação'
          AND date(s.data_contato) BETWEEN date(l.fim, '-1 year') AND l.fim
        GROUP BY mes, s.canal ORDER BY mes, s.canal""",
    ],
)
def test_allows_five_reference_aggregate_forms(sql):
    _policy().validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id AS canal, COUNT(*) FROM clientes GROUP BY id",
        "SELECT segredo AS categoria, COUNT(*) FROM compras GROUP BY segredo",
        "SELECT nome AS estado, COUNT(*) FROM clientes GROUP BY nome",
        "SELECT * FROM clientes",
        "SELECT canal FROM suporte",
    ],
)
def test_rejects_raw_or_disguised_row_projections(sql):
    policy = _policy()
    with pytest.raises(
        policy.SQLPolicyError, match="(?i)projeção|dimensão|privacidade|identificador|privada"
    ):
        policy.validate_sql(sql, SCHEMA)


def test_rejects_grouping_by_identifier_even_when_it_is_not_returned():
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="agrupamento|identificador"):
        policy.validate_sql("SELECT COUNT(*) FROM compras GROUP BY cliente_id", SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(DISTINCT email) FROM clientes",
        "SELECT MAX(nome) FROM clientes",
        "SELECT SUM(id) FROM compras",
        "SELECT AVG(idade) FROM clientes",
        "SELECT MAX(documento) FROM clientes",
        "SELECT MAX(valor, segredo) AS vazamento FROM compras",
        "SELECT MIN(valor, segredo) AS vazamento FROM compras",
        "SELECT MAX(valor, canal) AS um_por_linha FROM compras",
        "SELECT ROUND(COUNT(*), 2, segredo) FROM compras",
        "SELECT CAST(COUNT(*) AS TEXT DEFAULT segredo) FROM compras",
        "SELECT strftime('%Y-%m', date(data_compra, 'start of month', segredo)) "
        "AS mes, COUNT(*) FROM compras GROUP BY mes",
        "SELECT GROUP_CONCAT(canal) FROM compras",
        "SELECT strftime('%Y-%m', email) AS mes, COUNT(*) FROM clientes GROUP BY mes",
    ],
)
def test_rejects_private_values_inside_aggregate_or_function(sql):
    policy = _policy()
    with pytest.raises(
        policy.SQLPolicyError, match="privada|protegida|permitida|projeção|identificador"
    ):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        """WITH raw AS (SELECT segredo AS categoria FROM compras)
        SELECT categoria, COUNT(*) FROM raw GROUP BY categoria""",
        """SELECT COUNT(*) FROM
        (SELECT email AS canal FROM clientes) AS raw""",
        """SELECT COUNT(*) FROM clientes
        WHERE EXISTS (SELECT nome FROM clientes)""",
    ],
)
def test_rejects_private_reads_nested_in_cte_or_subquery(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="privada|protegida|projeção|dimensão"):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "table",
    ["sqlite_master", "sqlite_schema", "sqlite_temp_master", "other_table"],
)
def test_rejects_tables_not_in_inspected_schema(table):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="(?i)tabela"):
        policy.validate_sql(f"SELECT COUNT(*) FROM {table}", SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM compras; SELECT COUNT(*) FROM clientes",
        "SELECT COUNT(*) FROM compras WHERE (",
        "SELECT ' FROM compras",
        "SELECT /* FROM compras",
        "DELETE FROM compras",
    ],
)
def test_rejects_non_single_or_invalid_select_with_recoverable_error(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="SQL inválido|Apenas uma consulta"):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    ("sql", "valid_value"),
    [
        ("SELECT COUNT(*) FROM compras AS p WHERE p.canal = 'app'", "App"),
        ("SELECT COUNT(*) FROM suporte WHERE tipo_contato = 'reclamacao'", "Reclamação"),
        ("SELECT COUNT(*) FROM compras AS p WHERE 'app' = p.canal", "App"),
        ("SELECT COUNT(*) FROM compras AS p WHERE (p.canal) = 'app'", "App"),
        ("SELECT COUNT(*) FROM compras AS p WHERE p.canal <> 'app'", "App"),
    ],
)
def test_rejects_unmatched_exact_category_value_with_valid_spelling(sql, valid_value):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match=valid_value):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM clientes WHERE email = 'synthetic@example.invalid'",
        "SELECT COUNT(*) FROM compras WHERE id = 1",
        "SELECT canal, COUNT(*) FROM compras GROUP BY canal ORDER BY id",
    ],
)
def test_rejects_private_or_identifier_filters_and_sorting(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="privada|protegida|identificador"):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT canal AS id, COUNT(*) FROM compras GROUP BY id",
        "SELECT COUNT(*) FROM pragma_table_info('clientes')",
        "SELECT canal, COUNT(*) FROM compras GROUP BY canal HAVING MAX(segredo) <> ''",
        "SELECT COUNT(*) FILTER (WHERE email LIKE 'a%') FROM clientes",
        "WITH compras AS (SELECT COUNT(*) AS n FROM clientes) SELECT COUNT(*) FROM compras",
        "SELECT canal, COUNT(*) OVER () AS n FROM compras",
        "SELECT COUNT(*) FROM compras WHERE canal IN sqlite_master",
        "SELECT COUNT(*) FROM compras WHERE canal IN clientes",
        "SELECT COUNT(*) FROM compras WHERE canal IN pragma_table_info('clientes')",
    ],
)
def test_rejects_ast_scope_and_function_bypasses(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError):
        policy.validate_sql(sql, SCHEMA)


def test_rejects_repeating_a_safe_cte_metric_once_per_raw_row():
    policy = _policy()
    sql = """WITH total AS (SELECT COUNT(*) AS n FROM clientes)
    SELECT total.n FROM compras CROSS JOIN total"""
    with pytest.raises(policy.SQLPolicyError, match="agregad|privacidade|projeção"):
        policy.validate_sql(sql, SCHEMA)


def test_primary_key_wins_over_categorical_name_and_values():
    policy = _policy()
    schema = {
        "tables": [
            {
                "name": "catalogo",
                "columns": [_column("canal", primary_key=True)],
                "categorical_values": {"canal": [{"value": "unique-1", "count": 1}]},
            }
        ]
    }
    with pytest.raises(policy.SQLPolicyError, match="identificador|agrupamento"):
        policy.validate_sql("SELECT canal, COUNT(*) FROM catalogo GROUP BY canal", schema)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM campanhas_marketing WHERE strftime('%Y', data_envio) = '2024'",
        "SELECT COUNT(*) FROM compras WHERE strftime('%m', data_compra) = '05'",
        "SELECT MAX(date(data_envio)) AS data_maxima FROM campanhas_marketing",
        "SELECT MIN(date(data_compra)) AS data_minima FROM compras",
    ],
)
def test_allows_calendar_part_filters_and_ungrouped_aggregate_event_dates(sql):
    _policy().validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT canal, MAX(data_compra) FROM compras GROUP BY canal",
        "SELECT MAX(data_compra) FROM compras GROUP BY canal",
    ],
)
def test_rejects_grouped_exact_aggregate_dates(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError):
        policy.validate_sql(sql, SCHEMA)


def test_rejects_min_max_of_numeric_affinity_because_sqlite_can_store_text():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE synthetic (valor REAL)")
        connection.execute("INSERT INTO synthetic VALUES ('private-text')")
        assert connection.execute("SELECT MAX(valor) FROM synthetic").fetchone() == (
            "private-text",
        )
    policy = _policy()
    for sql in ("SELECT MAX(valor) FROM compras", "SELECT MIN(valor) FROM compras"):
        with pytest.raises(policy.SQLPolicyError):
            policy.validate_sql(sql, SCHEMA)


def test_rejects_raw_date_extrema_because_sqlite_can_return_private_text():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE synthetic (data_compra TEXT)")
        connection.execute("INSERT INTO synthetic VALUES ('PERSON_SENTINEL')")
        assert connection.execute("SELECT MAX(data_compra) FROM synthetic").fetchone() == (
            "PERSON_SENTINEL",
        )
        assert connection.execute("SELECT MAX(date(data_compra)) FROM synthetic").fetchone() == (
            None,
        )
    policy = _policy()
    for sql in (
        "SELECT MAX(data_compra) AS ultima FROM compras",
        "SELECT MIN(data_compra) AS primeira FROM compras",
        "WITH d AS (SELECT MAX(data_compra) AS ultima FROM compras) SELECT COUNT(*) FROM d",
    ):
        with pytest.raises(policy.SQLPolicyError):
            policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT strftime('%Y', data_compra) AS ano, COUNT(*) FROM compras GROUP BY ano",
        "SELECT strftime('%m', data_compra) AS mes, COUNT(*) FROM compras GROUP BY mes",
        "SELECT COUNT(*) FROM compras GROUP BY strftime('%Y', data_compra)",
        "SELECT MAX(strftime('%Y-%m', data_compra)) AS ultimo_mes FROM compras",
        "SELECT canal, MAX(strftime('%Y-%m', data_compra)) AS ultimo_mes "
        "FROM compras GROUP BY canal",
        """WITH ultimo AS (
            SELECT MAX(strftime('%Y-%m', data_compra)) AS mes FROM compras
        ) SELECT p.canal, COUNT(*) FROM compras AS p CROSS JOIN ultimo AS u
          WHERE strftime('%Y-%m', p.data_compra) = u.mes GROUP BY p.canal""",
    ],
)
def test_allows_aggregated_calendar_buckets_and_last_month(sql):
    _policy().validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT strftime('%Y', data_compra) AS ano FROM compras",
        "SELECT strftime('%m', data_compra) AS mes, COUNT(*) FROM compras",
        "SELECT data_compra FROM compras GROUP BY data_compra",
    ],
)
def test_rejects_ungrouped_calendar_parts_and_exact_event_date_dimensions(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError):
        policy.validate_sql(sql, SCHEMA)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(COUNT(*) AS TEXT) FROM compras",
        "SELECT CAST(COUNT(*) AS BLOB) FROM compras",
        "SELECT CAST(COUNT(*) AS VARCHAR(30)) FROM compras",
    ],
)
def test_rejects_text_or_binary_casts_of_aggregate_metrics(sql):
    policy = _policy()
    with pytest.raises(policy.SQLPolicyError, match="CAST|numéric|permitid"):
        policy.validate_sql(sql, SCHEMA)
