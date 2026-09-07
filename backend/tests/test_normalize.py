from datetime import date

import pytest

from normalize import (
    Quantity,
    parse_period,
    parse_quantity,
    periods_relation,
    values_agree,
    values_comparable,
)


# --- quantities --------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,value,unit",
    [
        ("₹8,142 crore", 8_142e7, "INR"),
        ("Rs. 2,194.5 crore", 2_194.5e7, "INR"),
        ("1.5 lakh", 1.5e5, None),
        ("$3.2 billion", 3.2e9, "USD"),
        ("USD 450 million", 450e6, "USD"),
        ("US$ 1.2 bn", 1.2e9, "USD"),
        ("6.5%", 6.5, "percent"),
        ("6.5 per cent", 6.5, "percent"),
        ("650 bps", 6.5, "percent"),
        ("8,142.5", 8142.5, None),
    ],
)
def test_parse_quantity(raw, value, unit):
    q = parse_quantity(raw)
    assert q.value == pytest.approx(value)
    assert q.unit == unit


@pytest.mark.parametrize(
    "raw,value",
    [
        ("₹(452) Cr", -452e7),          # accounting negative, as printed in earnings decks
        ("Rs. (1,008) crore", -1008e7),
        ("(6.3%)", -6.3),
        ("384K Tons", 384e3),
        ("₹127Cr", 127e7),               # no space between number and scale
        ("₹8,142 Cr (1)", 8142e7),       # trailing footnote marker must not flip the sign
    ],
)
def test_financial_shorthand(raw, value):
    assert parse_quantity(raw).value == pytest.approx(value)


def test_unit_hint_supplies_scale_from_table_header():
    q = parse_quantity("2,194", unit_hint="(₹ in millions)")
    assert q.value == pytest.approx(2_194e6)
    assert q.unit == "INR"


def test_explicit_scale_beats_hint():
    q = parse_quantity("2,194 crore", unit_hint="(₹ in millions)")
    assert q.value == pytest.approx(2_194e7)


def test_bps_not_read_as_bare_count():
    assert parse_quantity("650 bps").dimension == "ratio"


# --- comparability -----------------------------------------------------------

def test_cross_currency_is_not_comparable():
    ok, reason = values_comparable(parse_quantity("₹100 crore"), parse_quantity("$120 million"))
    assert not ok and reason == "different_currency"


def test_currency_vs_percent_is_not_comparable():
    ok, reason = values_comparable(parse_quantity("₹100 crore"), parse_quantity("6.5%"))
    assert not ok and reason == "different_dimension"


def test_rounding_difference_still_agrees():
    assert values_agree(parse_quantity("6.5%"), parse_quantity("6.54%"))


def test_material_difference_does_not_agree():
    assert not values_agree(parse_quantity("6.5%"), parse_quantity("7.2%"))


def test_same_value_written_at_different_scales_agrees():
    assert values_agree(parse_quantity("₹8,142 crore"), parse_quantity("₹81,420 million"))


def test_missing_value_never_agrees():
    assert not values_agree(Quantity(None, None, None, ""), parse_quantity("6.5%"))


# --- periods -----------------------------------------------------------------

def test_indian_fy_ends_in_march():
    p = parse_period("FY24")
    assert (p.start, p.end) == (date(2023, 4, 1), date(2024, 3, 31))
    assert p.kind == "fy"


def test_fy_span_form():
    assert parse_period("FY2023-24").start == date(2023, 4, 1)
    assert parse_period("FY 2024-25").end == date(2025, 3, 31)


def test_bare_span_is_fiscal_in_indian_documents():
    p = parse_period("2024-25")
    assert (p.start, p.end) == (date(2024, 4, 1), date(2025, 3, 31))


def test_slash_written_fiscal_year():
    """The IMF writes FY2024/25 where Indian sources write FY25 — the same year."""
    slash, short = parse_period("FY2024/25"), parse_period("FY25")
    assert (slash.start, slash.end) == (date(2024, 4, 1), date(2025, 3, 31))
    assert (slash.start, slash.end) == (short.start, short.end)
    assert periods_relation(slash, short) == "equal"


def test_quarter_written_in_words():
    p = parse_period("the first quarter of FY2025/26")
    assert (p.start, p.end) == (date(2025, 4, 1), date(2025, 6, 30))
    assert p.kind == "quarter"


def test_q4_of_indian_fy_is_jan_to_march():
    p = parse_period("Q4 FY24")
    assert (p.start, p.end) == (date(2024, 1, 1), date(2024, 3, 31))


def test_q1_of_indian_fy_is_april_to_june():
    assert parse_period("Q1 FY24").start == date(2023, 4, 1)


def test_half_year():
    p = parse_period("H1 FY25")
    assert (p.start, p.end) == (date(2024, 4, 1), date(2024, 9, 30))


def test_calendar_year_differs_from_fiscal_year():
    cy, fy = parse_period("CY2024"), parse_period("FY2024")
    assert cy.start == date(2024, 1, 1) and fy.start == date(2023, 4, 1)
    assert periods_relation(cy, fy) == "overlap"


@pytest.mark.parametrize(
    "raw", ["as at 31 March 2024", "March 31, 2024", "31.03.2024", "31/03/2024"]
)
def test_instant_forms(raw):
    p = parse_period(raw)
    assert p.start == date(2024, 3, 31) and p.kind == "instant"


def test_month_year():
    p = parse_period("March 2024")
    assert (p.start, p.end) == (date(2024, 3, 1), date(2024, 3, 31))


def test_unparseable_period_is_unknown_not_guessed():
    p = parse_period("during the period under review")
    assert p.kind == "unknown" and p.start is None


def test_period_relations():
    assert periods_relation(parse_period("FY24"), parse_period("FY24")) == "equal"
    assert periods_relation(parse_period("FY24"), parse_period("FY23")) == "disjoint"
    assert periods_relation(parse_period("Q4 FY24"), parse_period("FY24")) == "overlap"
    assert periods_relation(parse_period("FY24"), parse_period("unknown")) == "unknown"


def test_unknown_period_never_claims_equality():
    """A missing period must not be silently treated as matching."""
    assert periods_relation(parse_period(""), parse_period("")) == "unknown"
