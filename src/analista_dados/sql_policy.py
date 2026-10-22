"""Fail-closed privacy checks for model-proposed SQLite SELECT statements.

The inspected schema is an allowlist, not a source of SQL to execute. This module
never rewrites a statement: callers must run the original SQL only after this check
and retain the SQLite read-only authorizer as the final execution boundary.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, TokenError

_CATEGORIES = frozenset({"canal", "tipo_contato", "categoria", "estado"})
_EVENT_DATES = frozenset(
    {
        "data_compra",
        "data_envio",
        "data_contato",
        "data_pedido",
        "data_venda",
        "data_atendimento",
    }
)
_FLAGS = frozenset({"interagiu", "resolvido"})
_MEASURES = frozenset(
    {
        "valor",
        "valor_total",
        "preco",
        "preco_unitario",
        "quantidade",
        "receita",
        "custo",
        "desconto",
    }
)
_NUMERIC_AFFINITIES = ("INT", "REAL", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE")
_NUMERIC_CASTS = frozenset(
    {
        exp.DataType.Type.INT,
        exp.DataType.Type.BIGINT,
        exp.DataType.Type.SMALLINT,
        exp.DataType.Type.TINYINT,
        exp.DataType.Type.FLOAT,
        exp.DataType.Type.DOUBLE,
        exp.DataType.Type.DECIMAL,
    }
)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


class SQLPolicyError(ValueError):
    """A proposed query is unsafe or outside the supported analytical subset."""


@dataclass(frozen=True)
class _Field:
    kind: str
    name: str
    table: str | None = None
    values: frozenset[str] | None = None


@dataclass(frozen=True)
class _Source:
    alias: str
    fields: dict[str, _Field]
    table: str | None = None


@dataclass(frozen=True)
class _Output:
    kind: str
    key: str | None
    field: _Field


@dataclass
class _Scope:
    sources: dict[str, _Source]
    outputs: dict[str, _Output]


def validate_sql(sql: str, schema: dict) -> None:
    """Validate one SELECT/WITH; raise SQLPolicyError without modifying *sql*.

    Only named, schema-approved categorical dimensions, event calendar buckets
    and aggregates may be returned. Unknown columns and SQL constructs fail
    closed. This is not k-anonymity: small groups can still be identifying.
    Event-date extrema require date() normalization so malformed text is NULL.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SQLPolicyError("SQL inválido; forneça uma consulta SELECT.")
    catalog = _catalog(schema)
    try:
        statements = sqlglot.parse(sql, read="sqlite", error_level=ErrorLevel.RAISE)
    except (ParseError, TokenError) as exc:
        raise SQLPolicyError("SQL inválido; revise a sintaxe da consulta.") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SQLPolicyError("Apenas uma consulta SELECT/WITH é permitida.")
    _validate_select(statements[0], catalog, {}, internal=False)


def _catalog(schema: dict) -> dict[str, dict[str, _Field]]:
    if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
        raise SQLPolicyError("Schema inválido para validar a privacidade da consulta.")
    catalog: dict[str, dict[str, _Field]] = {}
    for table in schema["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise SQLPolicyError("Schema inválido para validar tabelas.")
        table_name = table["name"].casefold()
        if table_name in catalog or not isinstance(table.get("columns"), list):
            raise SQLPolicyError("Schema inválido ou com tabela duplicada.")
        category_values = table.get("categorical_values", {})
        if not isinstance(category_values, dict):
            raise SQLPolicyError("Schema inválido para validar categorias.")
        folded_categories = {str(key).casefold(): value for key, value in category_values.items()}
        fields: dict[str, _Field] = {}
        for column in table["columns"]:
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                raise SQLPolicyError("Schema inválido para validar colunas.")
            name = column["name"].casefold()
            if name in fields:
                raise SQLPolicyError("Schema inválido ou com coluna duplicada.")
            raw_values = folded_categories.get(name)
            values: frozenset[str] | None = None
            if column.get("primary_key") or name == "id" or name.endswith("_id"):
                kind = "id"
            elif name in _CATEGORIES and isinstance(raw_values, list):
                if not all(
                    isinstance(item, dict) and isinstance(item.get("value"), str)
                    for item in raw_values
                ):
                    raise SQLPolicyError("Schema inválido para validar valores categóricos.")
                values = frozenset(item["value"] for item in raw_values)
                kind = "category"
            elif name in _EVENT_DATES:
                kind = "date"
            elif name in _FLAGS:
                kind = "flag"
            elif name in _MEASURES and any(
                affinity in str(column.get("type", "")).upper() for affinity in _NUMERIC_AFFINITIES
            ):
                kind = "number"
            else:
                kind = "private"
            fields[name] = _Field(kind, name, table_name, values)
        catalog[table_name] = fields
    return catalog


def _validate_select(
    query: exp.Select,
    catalog: dict[str, dict[str, _Field]],
    inherited_ctes: dict[str, dict[str, _Field]],
    *,
    internal: bool,
) -> dict[str, _Field]:
    if query.args.get("qualify") or query.args.get("windows") or query.args.get("into"):
        raise SQLPolicyError("Janela ou destino SQL não permitido pela política de privacidade.")
    # Windows and FILTER preserve row granularity or hide row predicates inside aggregates.
    for node in _local_nodes(query):
        if isinstance(node, (exp.Window, exp.Filter)):
            raise SQLPolicyError("Função de janela/FILTER não permitida na projeção agregada.")

    ctes = dict(inherited_ctes)
    with_clause = query.args.get("with_")
    if with_clause:
        if with_clause.args.get("recursive"):
            raise SQLPolicyError("CTE recursiva não permitida pela política de privacidade.")
        for cte in with_clause.expressions:
            if not isinstance(cte, exp.CTE) or not isinstance(cte.this, exp.Select):
                raise SQLPolicyError("CTE deve conter apenas SELECT agregado.")
            name = cte.alias_or_name.casefold()
            if not name or name in ctes or name in catalog or name.startswith("sqlite_"):
                raise SQLPolicyError("CTE duplicada ou com nome de tabela real não permitida.")
            if cte.args.get("alias") and cte.args["alias"].args.get("columns"):
                raise SQLPolicyError(
                    "Lista de colunas de CTE não permitida; use aliases explícitos."
                )
            ctes[name] = _validate_select(cte.this, catalog, ctes, internal=True)

    from_clause = query.args.get("from_")
    if not from_clause or not from_clause.this:
        raise SQLPolicyError("A consulta precisa ler uma tabela permitida do schema.")
    scope = _Scope({}, {})
    _add_source(from_clause.this, scope, catalog, ctes)
    for join in query.args.get("joins") or []:
        if not isinstance(join, exp.Join) or join.args.get("using") or join.args.get("method"):
            raise SQLPolicyError("Forma de JOIN não permitida pela política de privacidade.")
        _add_source(join.this, scope, catalog, ctes)
        if join.args.get("on"):
            _predicate(join.args["on"], scope, catalog, ctes, join=True)

    # SQLite does not make SELECT aliases available to WHERE and JOIN ON.
    if query.args.get("where"):
        _predicate(query.args["where"].this, scope, catalog, ctes, calendar_parts=True)

    outputs: list[_Output] = []
    result_fields: dict[str, _Field] = {}
    for projection in query.expressions:
        expression = projection.this if isinstance(projection, exp.Alias) else projection
        kind, key, field = _projection(expression, scope, catalog, ctes)
        output = _Output(kind, key, field)
        outputs.append(output)
        output_name = projection.alias_or_name.casefold()
        if output_name:
            if output_name in scope.outputs:
                raise SQLPolicyError("Aliases de projeção duplicados não permitidos.")
            scope.outputs[output_name] = output
            result_fields[output_name] = _Field(kind, output_name, values=field.values)
        elif internal:
            raise SQLPolicyError("Projeções de CTE/subconsulta precisam de alias explícito.")

    group_keys: set[str] = set()
    group = query.args.get("group")
    if group:
        for expression in group.expressions:
            kind, key = _group_dimension(expression, scope, catalog, ctes)
            if kind not in {"category", "month"} or key is None:
                raise SQLPolicyError("Agrupamento por identificador ou dimensão não aprovada.")
            group_keys.add(key)
    if query.args.get("having"):
        _predicate(query.args["having"].this, scope, catalog, ctes)

    if any(output.kind == "aggregate_date" for output in outputs):
        if group_keys or any(output.kind in {"category", "month"} for output in outputs):
            raise SQLPolicyError("MAX/MIN de data só é permitido como agregado sem dimensão.")

    if any(source.table is not None for source in scope.sources.values()) and not group_keys:
        locally_aggregated = any(
            isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max))
            for node in _local_nodes(query)
        )
        if not locally_aggregated:
            raise SQLPolicyError("Projeção sobre tabela real precisa ser agregada.")

    for output in outputs:
        if output.kind in {"category", "month"} and output.key not in group_keys:
            # A previously grouped CTE is a safe relation; a base table cannot
            # return one raw category per source row.
            if any(source.table is not None for source in scope.sources.values()):
                raise SQLPolicyError("Dimensão na projeção precisa constar no agrupamento.")

    order = query.args.get("order")
    if order:
        for ordered in order.expressions:
            expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            kind, _, _ = _value(expression, scope, catalog, ctes, aliases=True)
            if kind not in {"category", "month", "metric", "aggregate_date", "aggregate_month"}:
                raise SQLPolicyError("ORDER BY identificador ou coluna privada não permitido.")
    limit = query.args.get("limit")
    if limit and (not isinstance(limit.expression, exp.Literal) or not limit.expression.is_int):
        raise SQLPolicyError("LIMIT deve ser um inteiro literal.")
    if query.args.get("offset"):
        raise SQLPolicyError("OFFSET não permitido pela política de privacidade.")
    return result_fields


def _local_nodes(query: exp.Select):
    """Walk this SELECT but never mistake a nested SELECT for its parent scope."""
    stack = [query]
    while stack:
        node = stack.pop()
        if node is not query and isinstance(node, exp.Select):
            continue
        yield node
        stack.extend(
            child for child in node.iter_expressions() if isinstance(child, exp.Expression)
        )


def _add_source(
    target: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
) -> None:
    if isinstance(target, exp.Table):
        if (
            not isinstance(target.this, exp.Identifier)
            or target.args.get("db")
            or target.args.get("catalog")
        ):
            raise SQLPolicyError("Fonte de tabela ou função SQL não permitida.")
        name = target.name.casefold()
        if name.startswith("sqlite_") or name not in catalog and name not in ctes:
            raise SQLPolicyError(f"Tabela '{target.name}' não permitida pelo schema inspecionado.")
        fields = catalog[name] if name in catalog else ctes[name]
        table = name if name in catalog else None
        alias = target.alias_or_name.casefold()
    elif isinstance(target, exp.Subquery) and isinstance(target.this, exp.Select):
        alias = target.alias_or_name.casefold()
        if not alias:
            raise SQLPolicyError("Subconsulta em FROM precisa de alias.")
        fields = _validate_select(target.this, catalog, ctes, internal=True)
        table = None
    else:
        raise SQLPolicyError("Fonte de tabela ou subconsulta não permitida.")
    if not alias or alias in scope.sources:
        raise SQLPolicyError("Aliases de tabela ausentes ou duplicados.")
    scope.sources[alias] = _Source(alias, fields, table)


def _resolve(column: exp.Column, scope: _Scope, *, aliases: bool = False) -> tuple[_Field, str]:
    if column.name == "*":
        raise SQLPolicyError("Projeção de linhas com * não permitida.")
    name = column.name.casefold()
    if column.table:
        source = scope.sources.get(column.table.casefold())
        if source is None or name not in source.fields:
            raise SQLPolicyError(f"Coluna '{column.sql()}' não encontrada nas tabelas permitidas.")
        return source.fields[name], f"{source.alias}.{name}"
    matches = [source for source in scope.sources.values() if name in source.fields]
    if len(matches) == 1:
        return matches[0].fields[name], f"{matches[0].alias}.{name}"
    if len(matches) > 1:
        raise SQLPolicyError(f"Coluna '{column.name}' ambígua entre tabelas.")
    if aliases and name in scope.outputs:
        output = scope.outputs[name]
        return output.field, output.key or f"output.{name}"
    raise SQLPolicyError(f"Coluna '{column.name}' não encontrada nas tabelas permitidas.")


def _value(
    expression: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
    *,
    aliases: bool = False,
    allow_id: bool = False,
) -> tuple[str, str | None, _Field]:
    if isinstance(expression, exp.Paren):
        return _value(expression.this, scope, catalog, ctes, aliases=aliases, allow_id=allow_id)
    if isinstance(expression, exp.Column):
        field, key = _resolve(expression, scope, aliases=aliases)
        if field.kind == "private":
            raise SQLPolicyError(f"Leitura de coluna privada '{field.name}' não permitida.")
        if field.kind == "id" and not allow_id:
            raise SQLPolicyError(f"Uso de identificador '{field.name}' não permitido aqui.")
        return field.kind, key, field
    if isinstance(expression, exp.Literal):
        return "literal", None, _Field("literal", expression.this)
    if isinstance(expression, (exp.Window, exp.Filter)):
        raise SQLPolicyError("Função de janela/FILTER não permitida na projeção agregada.")
    if isinstance(expression, exp.Star):
        raise SQLPolicyError("Projeção de linhas com * não permitida.")
    if isinstance(expression, exp.Count):
        if expression.expressions:
            raise SQLPolicyError("COUNT com argumentos extras não permitido.")
        argument = expression.this
        if isinstance(argument, exp.Distinct):
            if len(argument.expressions) != 1:
                raise SQLPolicyError("COUNT DISTINCT deve referir uma coluna permitida.")
            argument = argument.expressions[0]
        if not isinstance(argument, exp.Star):
            if not isinstance(argument, exp.Column):
                raise SQLPolicyError("COUNT deve usar * ou coluna permitida diretamente.")
            _value(argument, scope, catalog, ctes, allow_id=True)
        return "metric", None, _Field("metric", "COUNT")
    if isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        if expression.expressions:
            raise SQLPolicyError(
                "Agregação com argumentos extras não permitida; evite leitura privada."
            )
        argument = expression.this
        kind, _, _ = _value(argument, scope, catalog, ctes)
        if isinstance(expression, (exp.Sum, exp.Avg)) and kind != "number":
            raise SQLPolicyError("Agregação numérica exige medida permitida, não coluna privada.")
        if isinstance(expression, (exp.Min, exp.Max)) and kind not in {"date", "month"}:
            raise SQLPolicyError(
                "MIN/MAX só são permitidos para data/mês de evento, não medida privada."
            )
        unwrapped = argument
        while isinstance(unwrapped, exp.Paren):
            unwrapped = unwrapped.this
        if kind == "date" and not isinstance(unwrapped, exp.Date):
            raise SQLPolicyError(
                "MAX/MIN de data crua não permitido; use MAX(date(coluna_data)) "
                "ou MIN(date(coluna_data))."
            )
        result = (
            "aggregate_date"
            if kind == "date"
            else ("aggregate_month" if kind == "month" else "metric")
        )
        return result, None, _Field(result, expression.key.upper())
    if isinstance(expression, exp.Date):
        if expression.expressions:
            raise SQLPolicyError(
                "DATE com modificadores extras não permitida; evite leitura privada."
            )
        kind, _, _ = _value(expression.this, scope, catalog, ctes)
        if kind not in {"date", "aggregate_date"}:
            raise SQLPolicyError(
                "DATE exige coluna de data ou limite agregado, não data literal. "
                "Para período relativo, calcule MAX(date(coluna_data)) AS fim "
                "em uma CTE e use date(fim, '-1 year') no filtro."
            )
        zone = expression.args.get("zone")
        if zone is not None and not isinstance(zone, exp.Literal):
            raise SQLPolicyError("Modificador DATE precisa ser literal.")
        return kind, None, _Field(kind, "date")
    if isinstance(expression, exp.TsOrDsToTimestamp):
        kind, _, _ = _value(expression.this, scope, catalog, ctes)
        if kind not in {"date", "aggregate_date"}:
            raise SQLPolicyError("Conversão de data exige coluna de evento permitida.")
        return kind, None, _Field(kind, "date")
    if isinstance(expression, exp.TimeToStr):
        kind, _, _ = _value(expression.this, scope, catalog, ctes)
        format_value = expression.args.get("format")
        if (
            kind != "date"
            or not isinstance(format_value, exp.Literal)
            or format_value.this not in {"%Y-%m", "%Y", "%m"}
        ):
            raise SQLPolicyError("Dimensão temporal permitida somente por ano ou mês.")
        key = expression.sql(dialect="sqlite").casefold()
        return "month", key, _Field("month", "month")
    if isinstance(expression, (exp.Round, exp.Cast)):
        allowed_args = (
            {"this", "decimals"}
            if isinstance(expression, exp.Round)
            else {
                "this",
                "to",
                "safe",
            }
        )
        if any(
            name not in allowed_args and value is not None
            for name, value in expression.args.items()
        ):
            raise SQLPolicyError("Função com argumento privado ou extra não permitida.")
        if isinstance(expression, exp.Cast):
            target = expression.args.get("to")
            if not isinstance(target, exp.DataType) or target.this not in _NUMERIC_CASTS:
                raise SQLPolicyError("CAST de métrica permitido somente para tipo numérico.")
        kind, _, _ = _value(expression.this, scope, catalog, ctes)
        if kind not in {"metric", "number", "literal"}:
            raise SQLPolicyError("Função numérica sobre coluna não permitida.")
        if isinstance(expression, exp.Round):
            digits = expression.args.get("decimals")
            if digits is not None and (not isinstance(digits, exp.Literal) or not digits.is_int):
                raise SQLPolicyError("ROUND exige precisão inteira literal.")
        return kind, None, _Field(kind, "numeric")
    if isinstance(expression, _ARITHMETIC):
        left, _, _ = _value(expression.this, scope, catalog, ctes)
        right, _, _ = _value(expression.expression, scope, catalog, ctes)
        if left not in {"metric", "number", "literal"} or right not in {
            "metric",
            "number",
            "literal",
        }:
            raise SQLPolicyError("Aritmética sobre coluna não permitida.")
        result = "metric" if "metric" in (left, right) else "number"
        return result, None, _Field(result, "numeric")
    if isinstance(expression, exp.Subquery) and isinstance(expression.this, exp.Select):
        fields = _validate_select(expression.this, catalog, ctes, internal=True)
        if len(fields) != 1:
            raise SQLPolicyError("Subconsulta escalar exige uma métrica com alias.")
        field = next(iter(fields.values()))
        if field.kind not in {"metric", "aggregate_date", "aggregate_month"}:
            raise SQLPolicyError("Subconsulta escalar precisa retornar uma métrica agregada.")
        return field.kind, None, field
    raise SQLPolicyError(f"Função/expressão SQL '{expression.key}' não permitida na projeção.")


def _projection(
    expression: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
) -> tuple[str, str | None, _Field]:
    kind, key, field = _value(expression, scope, catalog, ctes)
    if kind not in {"category", "month", "metric", "aggregate_date", "aggregate_month"}:
        raise SQLPolicyError("Projeção deve conter métrica agregada ou dimensão aprovada.")
    return kind, key, field


def _group_dimension(
    expression: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
) -> tuple[str, str | None]:
    try:
        kind, key, _ = _value(expression, scope, catalog, ctes, aliases=True)
    except SQLPolicyError as exc:
        raise SQLPolicyError("Agrupamento por identificador ou dimensão não aprovada.") from exc
    return kind, key


def _predicate(
    expression: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
    *,
    join: bool = False,
    calendar_parts: bool = False,
) -> None:
    if isinstance(expression, exp.Paren):
        _predicate(expression.this, scope, catalog, ctes, join=join, calendar_parts=calendar_parts)
    elif isinstance(expression, (exp.And, exp.Or)):
        _predicate(expression.this, scope, catalog, ctes, join=join, calendar_parts=calendar_parts)
        _predicate(
            expression.expression, scope, catalog, ctes, join=join, calendar_parts=calendar_parts
        )
    elif isinstance(expression, exp.Not):
        _predicate(expression.this, scope, catalog, ctes, join=join, calendar_parts=calendar_parts)
    elif isinstance(expression, _COMPARISONS):
        if join and isinstance(expression, exp.EQ) and _identifier_join(expression, scope):
            return
        _predicate_value(expression.this, scope, catalog, ctes, calendar_parts)
        _predicate_value(expression.expression, scope, catalog, ctes, calendar_parts)
        if isinstance(expression, (exp.EQ, exp.NEQ)):
            _category_equality(expression.this, expression.expression, scope)
            _category_equality(expression.expression, expression.this, scope)
    elif isinstance(expression, exp.Between):
        _predicate_value(expression.this, scope, catalog, ctes, calendar_parts)
        _predicate_value(expression.args["low"], scope, catalog, ctes, calendar_parts)
        _predicate_value(expression.args["high"], scope, catalog, ctes, calendar_parts)
    elif isinstance(expression, exp.In):
        _predicate_value(expression.this, scope, catalog, ctes, calendar_parts)
        if any(expression.args.get(key) for key in ("query", "field", "unnest")):
            raise SQLPolicyError("IN com tabela/função/subconsulta implícita não permitido.")
        for item in expression.expressions:
            _predicate_value(item, scope, catalog, ctes, calendar_parts)
            _category_equality(expression.this, item, scope)
    elif isinstance(expression, exp.Is):
        _predicate_value(expression.this, scope, catalog, ctes, calendar_parts)
        if not isinstance(expression.expression, exp.Null):
            raise SQLPolicyError("IS só é permitido com NULL.")
    elif isinstance(expression, exp.Exists):
        if not isinstance(expression.this, exp.Select):
            raise SQLPolicyError("EXISTS exige SELECT agregado.")
        _validate_select(expression.this, catalog, ctes, internal=True)
    else:
        raise SQLPolicyError(f"Predicado SQL '{expression.key}' não permitido.")


def _predicate_value(
    expression: exp.Expression,
    scope: _Scope,
    catalog: dict[str, dict[str, _Field]],
    ctes: dict[str, dict[str, _Field]],
    calendar_parts: bool,
) -> tuple[str, str | None, _Field]:
    # Calendar parts are scalar filter values here. Projections of the same
    # parts are checked separately for safe grouping by _validate_select.
    node = expression
    while isinstance(node, exp.Paren):
        node = node.this
    if calendar_parts and isinstance(node, exp.TimeToStr):
        format_value = node.args.get("format")
        if isinstance(format_value, exp.Literal) and format_value.this in {"%Y", "%m"}:
            kind, _, _ = _value(node.this, scope, catalog, ctes)
            if kind != "date":
                raise SQLPolicyError("Parte de calendário exige data de evento permitida.")
            return "calendar_part", None, _Field("calendar_part", "calendar_part")
    return _value(expression, scope, catalog, ctes)


def _category_equality(left: exp.Expression, right: exp.Expression, scope: _Scope) -> None:
    while isinstance(left, exp.Paren):
        left = left.this
    while isinstance(right, exp.Paren):
        right = right.this
    if (
        not isinstance(left, exp.Column)
        or not isinstance(right, exp.Literal)
        or not right.is_string
    ):
        return
    field, _ = _resolve(left, scope)
    if field.kind != "category" or field.values is None or right.this in field.values:
        return
    options = ", ".join(sorted(field.values)) or "nenhum valor cadastrado"
    raise SQLPolicyError(
        f"Valor categórico '{right.this}' não existe em {field.table}.{field.name}. "
        f"Use a grafia exata: {options}."
    )


def _identifier_join(expression: exp.EQ, scope: _Scope) -> bool:
    if not isinstance(expression.this, exp.Column) or not isinstance(
        expression.expression, exp.Column
    ):
        return False
    left, _ = _resolve(expression.this, scope)
    right, _ = _resolve(expression.expression, scope)
    if left.kind != "id" or right.kind != "id" or not left.table or not right.table:
        return False
    for primary, foreign in ((left, right), (right, left)):
        if primary.name == "id" and foreign.name in {
            f"{primary.table.removesuffix('s')}_id",
            f"{primary.table}_id",
        }:
            return True
    raise SQLPolicyError("JOIN por identificadores sem relação nominal permitida.")
