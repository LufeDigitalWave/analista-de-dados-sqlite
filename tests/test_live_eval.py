"""Offline regressions for the optional, paid live semantic evaluation."""

from __future__ import annotations

import io
import json
import sqlite3

import pytest

from analista_dados.models import Answer, ChartSpec, ModelTurn, QueryResult, ToolCall
from analista_dados.visualization import prepare_visualization
from evals import private_review
from evals.live_eval import (
    QUESTIONS,
    RecordingSQLite,
    _private_case_record,
    assess_case,
    load_reference_rows,
    main,
    run_evaluation,
    safe_sql_for_log,
)

REFERENCES = (
    (
        ("São Paulo", 6),
        ("Minas Gerais", 3),
        ("Santa Catarina", 3),
        ("Alagoas", 2),
        ("Espírito Santo", 2),
    ),
    ((17,),),
    (("Roupas", 2.2113), ("Viagens", 2.1622), ("Livros", 1.9759)),
    (("Telefone", 19), ("Chat", 18), ("E-mail", 14)),
    (
        ("2024-07", "Chat", 1),
        ("2024-07", "E-mail", 2),
        ("2024-07", "Telefone", 3),
        ("2024-08", "Chat", 4),
        ("2024-08", "E-mail", 5),
        ("2024-08", "Telefone", 6),
    ),
)

SQL = (
    "SELECT c.estado, COUNT(DISTINCT p.cliente_id) AS clientes "
    "FROM compras p JOIN clientes c ON c.id = p.cliente_id "
    "WHERE p.canal = 'App' AND p.data_compra >= '2025-05-01' "
    "AND p.data_compra < '2025-06-01' GROUP BY c.estado "
    "ORDER BY clientes DESC, c.estado LIMIT 5",
    "SELECT COUNT(DISTINCT cliente_id) AS clientes FROM campanhas_marketing "
    "WHERE canal = 'WhatsApp' AND interagiu = 1 "
    "AND data_envio >= '2024-01-01' AND data_envio < '2025-01-01'",
    "SELECT categoria, ROUND(CAST(COUNT(*) AS REAL) / COUNT(DISTINCT cliente_id), 4) "
    "AS compras_por_comprador FROM compras GROUP BY categoria",
    "SELECT canal, COUNT(*) AS reclamacoes FROM suporte "
    "WHERE tipo_contato = 'Reclamação' AND resolvido = 0 GROUP BY canal",
    "WITH limite AS (SELECT MAX(date(data_contato)) AS fim FROM suporte) "
    "SELECT strftime('%Y-%m', s.data_contato) AS mes, s.canal, "
    "COUNT(*) AS reclamacoes FROM suporte s CROSS JOIN limite l "
    "WHERE s.tipo_contato = 'Reclamação' "
    "AND date(s.data_contato) BETWEEN date(l.fim, '-1 year') AND l.fim "
    "GROUP BY mes, s.canal ORDER BY mes, s.canal",
)

COLUMNS = (
    ("estado", "clientes"),
    ("clientes",),
    ("categoria", "compras_por_comprador"),
    ("canal", "reclamacoes"),
    ("mes", "canal", "reclamacoes"),
)

ANSWERS = (
    "São Paulo lidera com 6 clientes; a tabela apresenta os cinco estados.",
    "Foram 17 clientes distintos que interagiram no WhatsApp em 2024.",
    "Roupas lidera com 2,2113 compras por comprador distinto da categoria.",
    "Telefone: 19; Chat: 18; E-mail: 14 reclamações não resolvidas.",
    "Foram 21 reclamações no período, com séries mensais para Chat, E-mail e Telefone.",
)


def _answer(case: int, rows: tuple[tuple, ...] | None = None, text: str | None = None) -> Answer:
    result = QueryResult(COLUMNS[case - 1], REFERENCES[case - 1] if rows is None else rows)
    chart = ChartSpec("line", "mes", "reclamacoes", "canal") if case == 5 else ChartSpec()
    return Answer(text or ANSWERS[case - 1], "", result, chart, ())


@pytest.mark.parametrize("case", range(1, 6))
def test_all_five_canonical_cases_pass(case: int) -> None:
    assert len(QUESTIONS) == 5
    outcome = assess_case(case, _answer(case), REFERENCES[case - 1], SQL[case - 1])
    assert outcome.status == "PASS", outcome.issues


def test_wrong_same_shape_aggregate_is_a_failure() -> None:
    wrong = _answer(2, ((18,),), "Foram 18 clientes distintos em 2024.")
    outcome = assess_case(2, wrong, REFERENCES[1], SQL[1])
    assert outcome.status == "FAIL"
    assert "aggregate_mismatch" in outcome.issues


def test_q2_count_all_fails_even_when_the_count_coincides() -> None:
    wrong_sql = SQL[1].replace("COUNT(DISTINCT cliente_id)", "COUNT(*)")
    outcome = assess_case(2, _answer(2), REFERENCES[1], wrong_sql)
    assert outcome.status == "FAIL"
    assert "q2_not_distinct_clients" in outcome.issues


def test_q2_counting_joined_customer_pk_is_equivalent() -> None:
    equivalent = (
        "SELECT COUNT(DISTINCT c.id) AS clientes FROM campanhas_marketing m "
        "JOIN clientes c ON c.id = m.cliente_id "
        "WHERE m.canal = 'WhatsApp' AND m.interagiu = 1 "
        "AND m.data_envio >= '2024-01-01' AND m.data_envio < '2025-01-01'"
    )
    outcome = assess_case(2, _answer(2), REFERENCES[1], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q2_unrecognized_distinct_column_requires_review_not_false_failure() -> None:
    alternative = SQL[1].replace("COUNT(DISTINCT cliente_id)", "COUNT(DISTINCT campanha_id)")
    outcome = assess_case(2, _answer(2), REFERENCES[1], alternative)
    assert outcome.status == "REVIEW"
    assert "q2_distinct_sql_review" in outcome.issues


def test_q1_missing_may_filter_cannot_pass_on_coincidental_aggregate() -> None:
    wrong_sql = SQL[0].replace(
        "AND p.data_compra >= '2025-05-01' AND p.data_compra < '2025-06-01'", ""
    )
    outcome = assess_case(1, _answer(1), REFERENCES[0], wrong_sql)
    assert outcome.status != "PASS"
    assert "q1_sql_review" in outcome.issues


@pytest.mark.parametrize("operator", ("<>", "!="))
def test_q1_negated_app_channel_cannot_pass_on_coincidental_aggregate(operator: str) -> None:
    wrong_sql = SQL[0].replace("p.canal = 'App'", f"p.canal {operator} 'App'")
    outcome = assess_case(1, _answer(1), REFERENCES[0], wrong_sql)

    assert outcome.status == "REVIEW"
    assert "q1_sql_review" in outcome.issues


def test_q1_date_literals_without_date_column_bounds_cannot_pass() -> None:
    wrong_sql = SQL[0].replace(
        "p.data_compra >= '2025-05-01' AND p.data_compra < '2025-06-01'",
        "p.data_compra IS NOT NULL AND '2025-05-01' < '2025-06-01'",
    )
    outcome = assess_case(1, _answer(1), REFERENCES[0], wrong_sql)
    assert outcome.status == "REVIEW"
    assert "q1_sql_review" in outcome.issues


def test_q2_missing_whatsapp_interaction_and_year_filters_cannot_pass() -> None:
    wrong_sql = "SELECT COUNT(DISTINCT cliente_id) FROM campanhas_marketing"
    outcome = assess_case(2, _answer(2), REFERENCES[1], wrong_sql)
    assert outcome.status != "PASS"
    assert "q2_filters_review" in outcome.issues


def test_q2_lower_year_bound_without_upper_bound_cannot_pass() -> None:
    wrong_sql = SQL[1].replace("AND data_envio < '2025-01-01'", "")
    outcome = assess_case(2, _answer(2), REFERENCES[1], wrong_sql)
    assert outcome.status == "REVIEW"
    assert "q2_filters_review" in outcome.issues


def test_q2_closed_2024_between_filter_is_recognized() -> None:
    equivalent = SQL[1].replace(
        "data_envio >= '2024-01-01' AND data_envio < '2025-01-01'",
        "data_envio BETWEEN '2024-01-01' AND '2024-12-31'",
    )
    outcome = assess_case(2, _answer(2), REFERENCES[1], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q2_equivalent_but_unrecognized_distinct_layout_requires_review() -> None:
    alternative = (
        "SELECT COUNT(*) FROM (SELECT cliente_id FROM campanhas_marketing "
        "WHERE canal = 'WhatsApp' AND interagiu = 1 "
        "AND data_envio >= '2024-01-01' AND data_envio < '2025-01-01' "
        "GROUP BY cliente_id)"
    )
    outcome = assess_case(2, _answer(2), REFERENCES[1], alternative)
    assert outcome.status == "REVIEW"
    assert "q2_distinct_sql_review" in outcome.issues


def test_q2_wrong_narrative_cannot_pass_with_correct_data() -> None:
    outcome = assess_case(2, _answer(2, text="Foram 18 clientes em 2024."), REFERENCES[1], SQL[1])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "Foram 17 clientes distintos entre 01/01/2024 e 31/12/2024.",
        "Foram 17 clientes distintos entre 2024-01-01 e 2024-12-31.",
        "Foram 17 clientes distintos de 01/01/2024 até 31/12/2024.",
    ),
)
def test_q2_full_2024_dates_are_period_not_extra_client_counts(text: str) -> None:
    outcome = assess_case(2, _answer(2, text=text), REFERENCES[1], SQL[1])

    assert outcome.status == "PASS", outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "Foram 17 clientes distintos que interagiram no WhatsApp em 2025.",
        "Foram 17 clientes distintos entre 02/01/2024 e 31/12/2024.",
        "Foram 17 clientes distintos entre 31/12/2024 e 01/01/2024.",
        "Foram 17 clientes distintos entre 01/01/2024 e 31/12/2025.",
        "Foram 17 clientes distintos em 01/01/2024 e 31/12/2024.",
        "Foram 17 clientes distintos nos dias 01/01/2024 e 31/12/2024.",
        "Foram 17 clientes distintos, mas não entre 01/01/2024 e 31/12/2024.",
        "Foram 17 clientes distintos entre 01/01/2024 e 31/12/2024; 3 voltaram.",
        "Foram 17 clientes distintos entre 01/01/2024 e 31/12/2024; talvez 18.",
        "Foram 17,5 clientes distintos em 2024.",
        "Foram 17.5 clientes distintos em 2024.",
    ),
)
def test_q2_wrong_or_ambiguous_period_or_extra_count_never_passes(text: str) -> None:
    outcome = assess_case(2, _answer(2, text=text), REFERENCES[1], SQL[1])

    assert outcome.status != "PASS", outcome.issues


def test_q2_wrong_total_with_full_dates_still_fails() -> None:
    outcome = assess_case(
        2,
        _answer(2, text="Foram 18 clientes distintos entre 01/01/2024 e 31/12/2024."),
        REFERENCES[1],
        SQL[1],
    )

    assert outcome.status == "FAIL", outcome.issues
    assert "answer_text_mismatch" in outcome.issues


def test_q2_spelled_out_total_with_full_dates_requires_review() -> None:
    outcome = assess_case(
        2,
        _answer(2, text="Foram dezessete clientes entre 01/01/2024 e 31/12/2024."),
        REFERENCES[1],
        SQL[1],
    )

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues


@pytest.mark.parametrize(
    ("case", "text"),
    [
        (2, "Foram -17 clientes distintos que interagiram no WhatsApp em 2024."),
        (3, "Roupas: -2,2113 compras por comprador distinto da categoria."),
        (4, "Telefone: -19; Chat: 18; E-mail: 14 reclamações não resolvidas."),
    ],
)
def test_negative_metric_in_prose_cannot_pass(case: int, text: str) -> None:
    outcome = assess_case(case, _answer(case, text=text), REFERENCES[case - 1], SQL[case - 1])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


@pytest.mark.parametrize(
    ("case", "text"),
    [
        (2, "Não houve 17 clientes que interagiram no WhatsApp em 2024."),
        (4, "Não houve reclamações não resolvidas por canal."),
        (5, "Não houve reclamações no período."),
    ],
)
def test_explicit_absence_claim_cannot_pass_with_positive_reference(case: int, text: str) -> None:
    outcome = assess_case(case, _answer(case, text=text), REFERENCES[case - 1], SQL[case - 1])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


def test_localized_absence_claim_requires_review_not_global_failure() -> None:
    answer = _answer(5, text="Não houve reclamações em junho; houve 21 no período.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])
    assert outcome.status == "REVIEW"
    assert "answer_text_review" in outcome.issues


def test_q1_alternate_cutoff_tie_needs_manual_review() -> None:
    alternative = REFERENCES[0][:-1] + (("Paraná", 2),)
    outcome = assess_case(1, _answer(1, alternative), REFERENCES[0], SQL[0])
    assert outcome.status == "REVIEW"
    assert "q1_tie_review" in outcome.issues


def test_q1_ties_can_change_order_and_cutoff_without_being_wrong() -> None:
    alternative = (
        ("São Paulo", 6),
        ("Santa Catarina", 3),
        ("Minas Gerais", 3),
        ("Paraná", 2),
        ("Espírito Santo", 2),
    )
    outcome = assess_case(1, _answer(1, alternative), REFERENCES[0], SQL[0])
    assert outcome.status == "REVIEW"
    assert "q1_tie_review" in outcome.issues


def test_q1_counting_joined_customer_pk_is_equivalent_to_purchase_customer_id() -> None:
    equivalent = SQL[0].replace("COUNT(DISTINCT p.cliente_id)", "COUNT(DISTINCT c.id)")
    outcome = assess_case(1, _answer(1), REFERENCES[0], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q1_table_can_carry_the_ranking_without_repeating_numbers_in_prose() -> None:
    answer = _answer(1, text="Os cinco estados com mais clientes estão na tabela.")
    outcome = assess_case(1, answer, REFERENCES[0], SQL[0])
    assert outcome.status == "PASS", outcome.issues


def test_q3_denominator_must_be_explained() -> None:
    outcome = assess_case(
        3, _answer(3, text="Roupas lidera com 2,2113 compras."), REFERENCES[2], SQL[2]
    )
    assert outcome.status == "REVIEW"
    assert "q3_denominator_review" in outcome.issues


def test_q3_wrong_number_for_a_second_named_category_cannot_pass() -> None:
    narrative = (
        "Roupas lidera com 2,2113 compras por comprador distinto da categoria; "
        "Viagens teve 999 compras por comprador."
    )
    outcome = assess_case(3, _answer(3, text=narrative), REFERENCES[2], SQL[2])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


@pytest.mark.parametrize(
    ("case", "text"),
    [
        (1, "São Paulo: 6; a tabela contém São Paulo: 999 clientes."),
        (
            3,
            "Roupas lidera com 2,2113 compras por comprador distinto da categoria; "
            "Viagens também aparece; Viagens: 999 compras por comprador.",
        ),
        (4, "Telefone: 19; Chat: 18; E-mail: 14; Telefone: 999 reclamações."),
    ],
)
def test_repeated_label_with_contradictory_number_cannot_pass(case: int, text: str) -> None:
    outcome = assess_case(case, _answer(case, text=text), REFERENCES[case - 1], SQL[case - 1])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


def test_multiple_labels_followed_by_a_number_list_need_review_not_false_failure() -> None:
    answer = _answer(
        1,
        text="São Paulo, Minas Gerais e Santa Catarina tiveram 6, 3 e 3 clientes; tabela abaixo.",
    )
    outcome = assess_case(1, answer, REFERENCES[0], SQL[0])
    assert outcome.status == "REVIEW"
    assert "answer_text_review" in outcome.issues


def test_q3_two_decimal_rounding_is_accepted_when_sql_uses_distinct_buyers() -> None:
    rounded_rows = tuple((label, round(value, 2)) for label, value in REFERENCES[2])
    sql = SQL[2].replace("), 4)", "), 2)")
    answer = _answer(
        3,
        rounded_rows,
        "Roupas lidera com 2,21 compras por comprador distinto da categoria.",
    )
    outcome = assess_case(3, answer, REFERENCES[2], sql)
    assert outcome.status == "PASS", outcome.issues


def test_q5_equivalent_date_wrapper_on_upper_bound_passes() -> None:
    equivalent = SQL[4].replace(
        "BETWEEN date(l.fim, '-1 year') AND l.fim",
        "BETWEEN date(l.fim, '-1 year') AND date(l.fim)",
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q5_datetime_chart_axis_matches_reference_months() -> None:
    answer = _answer(5)
    assert answer.data is not None
    chart = prepare_visualization(answer.data, answer.chart)
    assert list(chart.frame["mes"].dt.strftime("%Y-%m")) == ["2024-07", "2024-08"]

    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])
    assert outcome.status == "PASS", outcome.issues


def test_q5_relative_lower_and_upper_bounds_projected_from_one_cte_pass() -> None:
    equivalent = (
        "WITH limites AS (SELECT MAX(date(data_contato)) AS fim, "
        "date(MAX(date(data_contato)), '-1 year') AS inicio FROM suporte) "
        "SELECT strftime('%Y-%m', s.data_contato) AS mes, s.canal, "
        "COUNT(*) AS reclamacoes FROM suporte AS s CROSS JOIN limites AS l "
        "WHERE s.tipo_contato = 'Reclamação' "
        "AND date(s.data_contato) BETWEEN l.inicio AND l.fim "
        "GROUP BY mes, s.canal ORDER BY mes, s.canal"
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q5_filtered_anchor_cannot_pass_when_aggregate_coincidentally_matches() -> None:
    filtered = SQL[4].replace(
        "SELECT MAX(date(data_contato)) AS fim FROM suporte",
        "SELECT MAX(date(data_contato)) AS fim FROM suporte WHERE tipo_contato = 'Reclamação'",
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], filtered)
    assert outcome.status == "REVIEW"
    assert "q5_anchor_review" in outcome.issues


def test_q5_equivalent_half_open_last_day_window_passes() -> None:
    equivalent = SQL[4].replace(
        "BETWEEN date(l.fim, '-1 year') AND l.fim",
        ">= date(l.fim, '-1 year') AND date(s.data_contato) < date(l.fim, '+1 day')",
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], equivalent)
    assert outcome.status == "PASS", outcome.issues


def test_q3_table_can_carry_the_ranking_without_numbers_in_prose() -> None:
    answer = _answer(
        3,
        text=(
            "A tabela mostra as categorias por média de compras "
            "por comprador distinto da categoria."
        ),
    )
    outcome = assess_case(3, answer, REFERENCES[2], SQL[2])
    assert outcome.status == "PASS", outcome.issues


def test_q5_text_can_describe_twelve_months_without_claiming_total() -> None:
    answer = _answer(5, text="A tendência de reclamações nos 12 meses está na série mensal.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])
    assert outcome.status == "PASS", outcome.issues


def test_q5_explicit_wrong_total_still_fails() -> None:
    answer = _answer(5, text="Foram 99 reclamações no total do período.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])
    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


def test_q5_correct_local_counts_are_not_mistaken_for_global_total() -> None:
    answer = _answer(5, text="Foram 5 reclamações no Chat; 7 por E-mail e 9 por Telefone.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "PASS", outcome.issues


def test_q5_full_period_channel_count_still_passes() -> None:
    answer = _answer(5, text="No último ano, Chat teve 5 reclamações.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "PASS", outcome.issues


def test_q5_explicit_full_twelve_months_still_passes() -> None:
    answer = _answer(5, text="Nos últimos 12 meses, Chat teve 5 reclamações.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "PASS", outcome.issues


def test_q5_wrong_channel_counts_fail_even_when_series_matches() -> None:
    answer = _answer(
        5,
        text="Telefone teve 999 reclamações; Chat teve 7 e E-mail teve 9.",
    )
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "FAIL"
    assert "answer_text_mismatch" in outcome.issues


def test_q5_number_shared_between_channels_needs_review() -> None:
    answer = _answer(5, text="Chat e E-mail tiveram 5 reclamações; Telefone está na tabela.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "REVIEW"
    assert "answer_text_review" in outcome.issues


def test_q5_coordinated_channel_sum_is_not_a_single_channel_count() -> None:
    reference = (("2024-07", "Chat", 5), ("2024-07", "E-mail", 7))
    answer = Answer(
        "Foram 12 reclamações no Chat e E-mail.",
        "",
        QueryResult(COLUMNS[4], reference),
        ChartSpec("line", "mes", "reclamacoes", "canal"),
        (),
    )
    outcome = assess_case(5, answer, reference, SQL[4])

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues
    assert "answer_text_mismatch" not in outcome.issues


def test_q5_wrong_count_for_one_explicit_channel_still_fails() -> None:
    answer = _answer(5, text="Foram 99 reclamações no Chat.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "FAIL", outcome.issues
    assert "answer_text_mismatch" in outcome.issues


def test_q5_local_total_with_unparsed_wording_needs_review() -> None:
    answer = _answer(5, text="Chat: total de 5 reclamações. Os demais estão na tabela.")
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])

    assert outcome.status == "REVIEW"
    assert "answer_text_review" in outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "Foram 999,5 reclamações no período.",
        "Chat: 5,9 reclamações; E-mail: 7; Telefone: 9.",
        "Chat: 5.9 reclamações; E-mail: 7; Telefone: 9.",
        "Foram 5,9 reclamações no Chat.",
    ),
)
def test_q5_fractional_count_requires_review_without_matching_integer_prefix(text: str) -> None:
    outcome = assess_case(5, _answer(5, text=text), REFERENCES[4], SQL[4])

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues
    assert "answer_text_mismatch" not in outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "Chat teve cinco reclamações.",
        "Foram vinte e uma reclamações no período.",
        "Chat: 5 mil reclamações.",
        "Chat: 5% das reclamações.",
    ),
)
def test_q5_unparsed_quantities_require_review(text: str) -> None:
    outcome = assess_case(5, _answer(5, text=text), REFERENCES[4], SQL[4])

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "Em julho de 2024, Chat teve 1 reclamação.",
        "Em 2024, Chat teve 1 reclamação.",
        "Em julho de 2024, houve 1 reclamação.",
    ),
)
def test_q5_time_scoped_count_is_not_compared_with_full_period(text: str) -> None:
    outcome = assess_case(5, _answer(5, text=text), REFERENCES[4], SQL[4])

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues
    assert "answer_text_mismatch" not in outcome.issues


@pytest.mark.parametrize(
    "text",
    (
        "No primeiro trimestre, Chat teve 1 reclamação.",
        "No primeiro semestre, Chat teve 1 reclamação.",
        "Na primeira semana, Chat teve 5 reclamações.",
        "No primeiro dia, Chat teve 5 reclamações.",
        "No mês, Chat teve 1 reclamação.",
        "Nos 2 meses, Chat teve 1 reclamação.",
    ),
)
def test_q5_subannual_count_requires_review_with_synthetic_series(text: str) -> None:
    reference = (("2024-01", "Chat", 1), ("2024-07", "Chat", 4))
    answer = Answer(
        text,
        "",
        QueryResult(COLUMNS[4], reference),
        ChartSpec("line", "mes", "reclamacoes", "canal"),
        (),
    )
    outcome = assess_case(5, answer, reference, SQL[4])

    assert outcome.status == "REVIEW", outcome.issues
    assert "answer_text_review" in outcome.issues
    assert "answer_text_mismatch" not in outcome.issues


def test_q5_unrecognized_relative_period_requires_review_not_false_failure() -> None:
    alternative = SQL[4].replace("'-1 year'", "'-365 days'")
    outcome = assess_case(5, _answer(5), REFERENCES[4], alternative)
    assert outcome.status == "REVIEW"
    assert "q5_period_sql_review" in outcome.issues


@pytest.mark.parametrize(
    "wrong_sql",
    [
        SQL[4].replace("date(l.fim, '-1 year')", "date(l.fim, '-1 year', '+1 day')"),
        SQL[4].replace("BETWEEN date(l.fim, '-1 year')", "> date(l.fim, '-1 year')"),
    ],
)
def test_q5_wrong_period_sql_fails_even_if_all_rows_coincide(wrong_sql: str) -> None:
    outcome = assess_case(5, _answer(5), REFERENCES[4], wrong_sql)
    assert outcome.status == "FAIL"
    assert "q5_not_relative_period" in outcome.issues


def test_q5_extra_strict_lower_bound_cannot_pass_because_a_between_also_exists() -> None:
    wrong_sql = SQL[4].replace(
        "GROUP BY mes, s.canal",
        "AND date(s.data_contato) > date(l.fim, '-1 year') GROUP BY mes, s.canal",
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], wrong_sql)
    assert outcome.status == "FAIL"
    assert "q5_not_relative_period" in outcome.issues


@pytest.mark.parametrize("case", (4, 5))
def test_or_predicate_never_passes_automatic_semantic_gate(case: int) -> None:
    wrong_sql = SQL[case - 1].replace("WHERE ", "WHERE 1 = 1 OR ", 1)
    outcome = assess_case(case, _answer(case), REFERENCES[case - 1], wrong_sql)
    assert outcome.status == "REVIEW"
    assert "or_predicate_review" in outcome.issues


@pytest.mark.parametrize(
    ("case", "negated_sql"),
    (
        (1, SQL[0].replace("p.canal = 'App'", "NOT (p.canal = 'App')")),
        (2, SQL[1].replace("interagiu = 1", "NOT (interagiu = 1)")),
        (4, SQL[3].replace("resolvido = 0", "NOT (resolvido = 0)")),
    ),
)
def test_negated_sql_predicate_requires_review_when_aggregate_coincides(
    case: int, negated_sql: str
) -> None:
    outcome = assess_case(case, _answer(case), REFERENCES[case - 1], negated_sql)

    assert outcome.status == "REVIEW", outcome.issues
    assert "negated_predicate_review" in outcome.issues
    assert outcome.aggregate_match


def test_negated_sql_predicate_does_not_hide_aggregate_mismatch() -> None:
    negated_sql = SQL[1].replace("interagiu = 1", "NOT (interagiu = 1)")
    outcome = assess_case(2, _answer(2, rows=((18,),)), REFERENCES[1], negated_sql)

    assert outcome.status == "FAIL", outcome.issues
    assert "aggregate_mismatch" in outcome.issues
    assert "negated_predicate_review" in outcome.issues


def test_q4_null_as_unresolved_fails_even_when_aggregates_coincide() -> None:
    wrong_sql = SQL[3].replace("resolvido = 0", "(resolvido = 0 OR resolvido IS NULL)")
    outcome = assess_case(4, _answer(4), REFERENCES[3], wrong_sql)

    assert outcome.status == "FAIL"
    assert "q4_null_expansion" in outcome.issues


def test_q4_negated_null_predicate_needs_review_not_false_failure() -> None:
    equivalent_sql = SQL[3].replace("resolvido = 0", "NOT (resolvido IS NULL OR resolvido = 1)")
    outcome = assess_case(4, _answer(4), REFERENCES[3], equivalent_sql)

    assert outcome.status == "REVIEW"
    assert "or_predicate_review" in outcome.issues
    assert "q4_null_expansion" not in outcome.issues


def test_q4_explicit_non_null_guard_needs_review_not_false_failure() -> None:
    equivalent_sql = SQL[3].replace(
        "resolvido = 0",
        "(resolvido = 0 OR resolvido IS NULL) AND resolvido IS NOT NULL",
    )
    outcome = assess_case(4, _answer(4), REFERENCES[3], equivalent_sql)

    assert outcome.status == "REVIEW"
    assert "or_predicate_review" in outcome.issues
    assert "q4_null_expansion" not in outcome.issues


def test_q4_count_nonnull_column_needs_review_not_false_failure() -> None:
    alternative_sql = (
        SQL[3]
        .replace("COUNT(*)", "COUNT(resolvido)")
        .replace("resolvido = 0", "(resolvido = 0 OR resolvido IS NULL)")
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE suporte (canal TEXT, tipo_contato TEXT, resolvido INTEGER)"
        )
        connection.executemany(
            "INSERT INTO suporte VALUES (?, ?, ?)",
            [
                ("Chat", "Reclamação", 0),
                ("Chat", "Reclamação", 1),
                ("Chat", "Reclamação", None),
            ],
        )
        assert (
            connection.execute(alternative_sql).fetchall() == connection.execute(SQL[3]).fetchall()
        )

    outcome = assess_case(4, _answer(4), REFERENCES[3], alternative_sql)
    assert outcome.status == "REVIEW"
    assert "q4_null_expansion" not in outcome.issues


def test_q4_explicitly_excluding_null_remains_equivalent() -> None:
    equivalent_sql = SQL[3].replace("GROUP BY canal", "AND resolvido IS NOT NULL GROUP BY canal")
    outcome = assess_case(4, _answer(4), REFERENCES[3], equivalent_sql)

    assert outcome.status == "PASS", outcome.issues


def test_q5_literal_date_cutoff_needs_review_without_probe_history() -> None:
    literal = SQL[4].replace("date(l.fim, '-1 year')", "date('2024-07-23')")
    outcome = assess_case(5, _answer(5), REFERENCES[4], literal)
    assert outcome.status == "REVIEW"
    assert "q5_period_sql_review" in outcome.issues


def test_q5_literal_bounds_derived_from_prior_max_date_probe_can_pass() -> None:
    probe = "SELECT MAX(date(data_contato)) AS fim FROM suporte"
    history = ((probe, QueryResult(("fim",), (("2025-07-22",),))),)
    final_sql = (
        "SELECT strftime('%Y-%m', s.data_contato) AS mes, s.canal, "
        "COUNT(*) AS reclamacoes FROM suporte AS s "
        "WHERE s.tipo_contato = 'Reclamação' "
        "AND s.data_contato BETWEEN '2024-07-22' AND '2025-07-22' "
        "GROUP BY mes, s.canal ORDER BY mes, s.canal"
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], final_sql, history=history)
    assert outcome.status == "PASS", outcome.issues

    wrong_sql = final_sql.replace("'2024-07-22'", "'2024-07-23'")
    wrong = assess_case(5, _answer(5), REFERENCES[4], wrong_sql, history=history)
    assert wrong.status == "FAIL"
    assert "q5_not_relative_period" in wrong.issues


def test_q5_missing_channel_month_point_fails() -> None:
    outcome = assess_case(5, _answer(5, REFERENCES[4][:-1]), REFERENCES[4], SQL[4])
    assert outcome.status == "FAIL"
    assert "aggregate_mismatch" in outcome.issues


def test_q5_relative_expression_not_applied_to_contact_date_fails() -> None:
    wrong_sql = SQL[4].replace(
        "date(s.data_contato) BETWEEN date(l.fim, '-1 year') AND l.fim",
        "date(s.data_contato) > date(l.fim, '-1 year', '+1 day') "
        "AND 1 BETWEEN date(l.fim, '-1 year') AND l.fim",
    )
    outcome = assess_case(5, _answer(5), REFERENCES[4], wrong_sql)
    assert outcome.status == "FAIL"
    assert "q5_not_relative_period" in outcome.issues


def test_q5_wide_but_equivalent_layout_cannot_pass_automatically() -> None:
    wide = QueryResult(
        ("mes", "Chat", "E-mail", "Telefone"),
        (("2024-07", 1, 2, 3), ("2024-08", 4, 5, 6)),
    )
    answer = Answer(ANSWERS[4], "", wide, ChartSpec("line", "mes", "Chat"), ())
    outcome = assess_case(5, answer, REFERENCES[4], SQL[4])
    assert outcome.status == "REVIEW"
    assert "layout_review" in outcome.issues


def test_selected_sql_is_the_original_evidence_query_not_the_last_query(tmp_path) -> None:
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE medidas (valor INTEGER)")
        connection.executemany("INSERT INTO medidas VALUES (?)", [(1,), (2,)])

    db = RecordingSQLite(path)
    db.inspect_schema()
    selected_sql = "SELECT COUNT(*) AS total FROM medidas"
    selected = db.run_sql(selected_sql)
    db.run_sql("SELECT SUM(valor) AS total FROM medidas")
    answer = Answer("Dois registros.", "", selected, ChartSpec(), ())
    assert db.sql_for(answer) == selected_sql


def test_empty_reference_fails_closed() -> None:
    outcome = assess_case(2, _answer(2), (), SQL[1])
    assert outcome.status == "FAIL"
    assert "empty_reference" in outcome.issues


class _ScriptedModel:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = iter(turns)

    def next_turn(self, history: list) -> ModelTurn:
        return next(self.turns)


def _turn(name: str, arguments: dict, index: int) -> ModelTurn:
    return ModelTurn(ToolCall(name, arguments, f"call-{index}"), [])


def test_run_evaluation_exits_nonzero_on_wrong_semantics_without_api(tmp_path) -> None:
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE campanhas_marketing "
            "(cliente_id INTEGER, canal TEXT, interagiu INTEGER, data_envio TEXT)"
        )
        connection.executemany(
            "INSERT INTO campanhas_marketing VALUES (?, 'WhatsApp', 1, '2024-06-01')",
            [(index,) for index in range(17)],
        )

    wrong_sql = SQL[1].replace("COUNT(DISTINCT cliente_id)", "COUNT(*)")
    turns = [
        _turn("inspect_schema", {}, 1),
        _turn("run_sql", {"sql": wrong_sql, "purpose": "Contar clientes"}, 2),
        _turn(
            "finish_answer",
            {
                "answer": ANSWERS[1],
                "query_number": 1,
                "visualization": "table",
                "x_column": "",
                "y_column": "",
                "series_column": "",
                "assumptions": [],
            },
            3,
        ),
    ]
    output = io.StringIO()
    exit_code = run_evaluation(
        path, REFERENCES, lambda _case: _ScriptedModel(turns), selected=(2,), out=output
    )
    assert exit_code == 1
    assert "q2_not_distinct_clients" in output.getvalue()
    assert "Foram 17 clientes" not in output.getvalue()


def test_reference_rows_are_loaded_independently_from_read_only_sqlite(tmp_path) -> None:
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE n (valor INTEGER)")
        connection.executemany("INSERT INTO n VALUES (?)", [(1,), (2,)])
    sql_path = tmp_path / "reference.sql"
    sql_path.write_text("SELECT COUNT(*) FROM n;" * 5, encoding="utf-8")
    assert load_reference_rows(path, queries_path=sql_path) == (((2,),),) * 5


def test_sql_log_redacts_untrusted_literals_comments_and_large_numbers() -> None:
    sql = (
        "SELECT COUNT(*) FROM clientes WHERE nome = 'Pessoa Fictícia' "
        "AND cliente_id = 12345678901 -- EXAMPLE_SECRET_LITERAL"
    )
    logged = safe_sql_for_log(sql)
    assert "SELECT" in logged
    assert "sha256=" in logged
    assert "Pessoa Fictícia" not in logged
    assert "12345678901" not in logged
    assert "EXAMPLE_SECRET_LITERAL" not in logged


def test_sql_log_does_not_leak_quoted_or_unquoted_identifiers() -> None:
    sql = (
        "SELECT COUNT(*) AS [EXAMPLE_PERSON_NAME], COUNT(*) AS `EXAMPLE_CPF` "
        "FROM compras AS EXAMPLE_PERSON_ALIAS"
    )
    logged = safe_sql_for_log(sql)
    assert "EXAMPLE_" not in logged
    assert "sha256=" in logged


def test_main_without_credentials_stops_before_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.db"))
    assert main(["2"]) == 3


def _synthetic_q2_database(tmp_path):
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE campanhas_marketing "
            "(cliente_id INTEGER, canal TEXT, interagiu INTEGER, data_envio TEXT)"
        )
        connection.executemany(
            "INSERT INTO campanhas_marketing VALUES (?, 'WhatsApp', 1, '2024-06-01')",
            [(index,) for index in range(17)],
        )
    return path


def _scripted_q2_model(_case: int) -> _ScriptedModel:
    return _ScriptedModel(
        [
            _turn("inspect_schema", {}, 1),
            _turn("run_sql", {"sql": SQL[1], "purpose": "Contar clientes"}, 2),
            _turn(
                "finish_answer",
                {
                    "answer": ANSWERS[1] + " MARCADOR_PRIVADO_SINTETICO",
                    "query_number": 1,
                    "visualization": "table",
                    "x_column": "",
                    "y_column": "",
                    "series_column": "",
                    "assumptions": [],
                },
                3,
            ),
        ]
    )


def _private_review_test_root(monkeypatch, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    repo = outputs / "repo"
    repo.mkdir()
    monkeypatch.setattr(private_review, "REPO_ROOT", repo)
    monkeypatch.setattr(private_review, "git_state", lambda: ("a" * 40, False))
    return repo


def test_private_review_is_opt_in_and_preserves_redacted_console(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    plain_output = io.StringIO()

    plain_code = run_evaluation(
        db_path, REFERENCES, _scripted_q2_model, selected=(2,), out=plain_output
    )

    assert plain_code == 0
    assert not (tmp_path / "work").exists()
    assert "MARCADOR_PRIVADO_SINTETICO" not in plain_output.getvalue()

    private_output = io.StringIO()
    private_code = run_evaluation(
        db_path,
        REFERENCES,
        _scripted_q2_model,
        selected=(2,),
        private_review=True,
        model_name="synthetic",
        out=private_output,
    )

    assert private_code == 0
    bundles = list((tmp_path / "work" / "private-review").glob("*.json"))
    assert len(bundles) == 1
    bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
    assert bundle["format_version"] == 1
    assert bundle["commit"] == "a" * 40
    assert bundle["dirty"] is False
    assert bundle["model"] == "synthetic"
    assert bundle["selected_cases"] == [2]
    assert bundle["attempted_cases"] == [2]
    assert bundle["completed_cases"] == 1
    assert bundle["error_code"] is None
    assert bundle["database_sha256_before"] == bundle["database_sha256_after"]
    record = bundle["cases"][0]
    assert record["status"] == "PASS"
    assert record["answer_text"] == ANSWERS[1] + " MARCADOR_PRIVADO_SINTETICO"
    assert record["selected_sql"] == SQL[1]
    assert record["successful_sql"] == [SQL[1]]
    assert record["result_columns"] == ["clientes"]
    assert record["result_rows"] == [[17]]
    assert "MARCADOR_PRIVADO_SINTETICO" not in private_output.getvalue()
    assert SQL[1] not in private_output.getvalue()
    assert str(db_path) not in bundles[0].read_text(encoding="utf-8")


def test_private_review_partial_error_redacts_exception_text(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)

    def model_factory(case: int):
        if case == 2:
            return _scripted_q2_model(case)
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        model_factory,
        selected=(2, 3, 4),
        private_review=True,
        model_name="synthetic",
        out=output,
    )

    assert code == 1
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["selected_cases"] == [2, 3, 4]
    assert bundle["attempted_cases"] == [2, 3]
    assert bundle["completed_cases"] == 1
    assert bundle["error_code"] == "execution_RuntimeError"
    assert bundle["cases"][1]["status"] == "FAIL"
    assert bundle["cases"][1]["answer_text"] is None
    assert bundle["cases"][1]["selected_sql"] is None
    assert bundle["cases"][1]["actual_row_count"] is None
    assert "PRIVATE_EXCEPTION_SENTINEL" not in bundle_path.read_text(encoding="utf-8")
    assert "PRIVATE_EXCEPTION_SENTINEL" not in output.getvalue()


def test_private_review_distinguishes_empty_result_from_missing_result(tmp_path) -> None:
    answer = _answer(2, rows=())
    outcome = assess_case(2, answer, REFERENCES[1], SQL[1])

    record = _private_case_record(
        2, outcome, answer, SQL[1], RecordingSQLite(tmp_path / "synthetic.db")
    )

    assert record["actual_row_count"] == 0


def test_private_review_mismatched_aggregate_does_not_store_rows(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    references = list(REFERENCES)
    references[1] = ((18,),)

    code = run_evaluation(
        db_path,
        references,
        _scripted_q2_model,
        selected=(2,),
        private_review=True,
        out=io.StringIO(),
    )

    assert code == 1
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    record = json.loads(bundle_path.read_text(encoding="utf-8"))["cases"][0]
    assert record["aggregate_match"] is False
    assert record["actual_row_count"] == 1
    assert record["result_rows"] is None
    assert record["result_columns"] is None


def test_private_review_write_failure_is_not_reported_as_success(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)

    def fail_write(_payload):
        raise OSError("PRIVATE_FILE_ERROR_SENTINEL")

    monkeypatch.setattr(private_review, "write_private_review", fail_write)
    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        _scripted_q2_model,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 3
    assert "PRIVATE_REVIEW_FAIL" in output.getvalue()
    assert "PRIVATE_FILE_ERROR_SENTINEL" not in output.getvalue()


def test_private_review_preflight_failure_creates_no_bundle(monkeypatch, tmp_path) -> None:
    _private_review_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.db"))

    assert main(["--private-review", "2"]) == 3
    assert not (tmp_path / "work").exists()


def test_private_review_empty_selected_reference_stops_before_model_and_bundle(
    monkeypatch, tmp_path
) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    references = list(REFERENCES)
    references[1] = ()
    called_cases: list[int] = []

    def model_factory(case: int):
        called_cases.append(case)
        return _scripted_q2_model(case)

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        references,
        model_factory,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 3
    assert called_cases == []
    assert "REFERENCE_FAIL: empty_reference" in output.getvalue()
    assert not (tmp_path / "work").exists()


def test_main_empty_selected_reference_stops_before_provider(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-only")
    references = list(REFERENCES)
    references[1] = ()
    monkeypatch.setattr("evals.live_eval.load_reference_rows", lambda _path: references)

    def forbidden_provider(**_kwargs):
        raise AssertionError("PROVIDER_MUST_NOT_START")

    monkeypatch.setattr("openai.OpenAI", forbidden_provider)

    assert main(["--private-review", "2"]) == 3
    assert not (tmp_path / "work").exists()


def test_private_review_dirty_tree_stops_before_model_call(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    monkeypatch.setattr(private_review, "git_state", lambda: ("a" * 40, True))

    def model_factory(_case: int):
        raise AssertionError("MODEL_SHOULD_NOT_BE_CALLED")

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        model_factory,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 3
    assert "dirty_tree" in output.getvalue()
    assert "MODEL_SHOULD_NOT_BE_CALLED" not in output.getvalue()
    assert not list((tmp_path / "work" / "private-review").glob("*.json"))


def test_private_review_changed_database_is_not_complete(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    hashes = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(private_review, "sha256_file", lambda _path: next(hashes))

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        _scripted_q2_model,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 3
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["database_sha256_before"] == "a" * 64
    assert bundle["database_sha256_after"] == "b" * 64
    assert bundle["integrity_issues"] == ["database_changed"]
    assert "database_changed" in output.getvalue()


def test_private_review_changed_source_is_not_reproducible(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    states = iter((("a" * 40, False), ("a" * 40, True)))
    monkeypatch.setattr(private_review, "git_state", lambda: next(states))

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        _scripted_q2_model,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 3
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["commit_after"] == "a" * 40
    assert bundle["dirty_after"] is True
    assert bundle["integrity_issues"] == ["source_changed"]
    assert "source_changed" in output.getvalue()


def test_private_review_keyboard_interrupt_saves_categorical_failure(monkeypatch, tmp_path) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)

    def model_factory(_case: int):
        raise KeyboardInterrupt

    output = io.StringIO()
    code = run_evaluation(
        db_path,
        REFERENCES,
        model_factory,
        selected=(2,),
        private_review=True,
        out=output,
    )

    assert code == 1
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["attempted_cases"] == [2]
    assert bundle["completed_cases"] == 0
    assert bundle["error_code"] == "execution_KeyboardInterrupt"
    assert bundle["cases"][0]["answer_text"] is None


def test_private_review_keeps_execution_error_if_database_also_changes(
    monkeypatch, tmp_path
) -> None:
    db_path = _synthetic_q2_database(tmp_path)
    _private_review_test_root(monkeypatch, tmp_path)
    hashes = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(private_review, "sha256_file", lambda _path: next(hashes))

    def model_factory(_case: int):
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    code = run_evaluation(
        db_path,
        REFERENCES,
        model_factory,
        selected=(2,),
        private_review=True,
        out=io.StringIO(),
    )

    assert code == 3
    bundle_path = next((tmp_path / "work" / "private-review").glob("*.json"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["error_code"] == "execution_RuntimeError"
    assert bundle["integrity_issues"] == ["database_changed"]
