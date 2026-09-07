"""The deterministic half of the comparison engine, which runs with no LLM."""

from match import classify
from normalize import parse_period, parse_quantity


def fact(value_raw, period_raw, doc_id=1, subject="India", predicate="real GDP growth",
         unit_hint=None):
    q = parse_quantity(value_raw, unit_hint) if value_raw else parse_quantity("")
    p = parse_period(period_raw)
    return {
        "doc_id": doc_id, "page": 1, "subject": subject, "predicate": predicate,
        "qualifiers": "[]", "value_raw": value_raw, "value_num": q.value,
        "unit": q.unit, "dimension": q.dimension,
        "period_raw": period_raw,
        "period_start": p.start.isoformat() if p.start else None,
        "period_end": p.end.isoformat() if p.end else None,
        "period_kind": p.kind,
        "source_quote": "q", "filename": "f.pdf", "title": "T",
    }


def test_same_period_same_value_corroborates_without_llm():
    t, expl, _ = classify(fact("6.5%", "FY25"), fact("6.5 per cent", "FY2024-25"))
    assert t == "corroborates"
    assert "agree" in expl


def test_rounding_difference_still_corroborates():
    t, _, _ = classify(fact("6.5%", "FY25"), fact("6.54%", "FY25"))
    assert t == "corroborates"


def test_same_value_different_scale_corroborates():
    t, _, _ = classify(
        fact("₹8,142 crore", "FY24", predicate="revenue"),
        fact("₹81,420 million", "FY24", predicate="revenue"),
    )
    assert t == "corroborates"


def test_differing_values_over_disjoint_periods_defer_to_llm():
    """No deterministic 'reconciled by time' rule — see the next test for why."""
    t, _, _ = classify(
        fact("₹8,142 Cr", "FY24", predicate="revenue"),
        fact("₹2,076 Cr", "Q4 FY23", predicate="revenue"),
    )
    assert t is None


def test_unrelated_metrics_at_different_dates_are_never_auto_reconciled():
    """The regression this rule removal exists for.

    'Total equity (FY21)' and 'Total assets (FY23)' differ in value and period, which a
    disjoint-period rule would happily call "reconciled by time" — asserting these are
    the same claim on no evidence at all. Only the model can rule on this pair.
    """
    t, _, _ = classify(
        fact("48,465.92", "FY21", predicate="total equity"),
        fact("112,134.30", "FY23", predicate="total assets"),
    )
    assert t is None


def test_cross_currency_defers_and_flags_the_fx_problem():
    t, _, signals = classify(
        fact("₹3,00,000 crore", "FY24", predicate="GDP"),
        fact("$3.9 trillion", "FY24", predicate="GDP"),
    )
    assert t is None
    assert "exchange rate" in signals
    assert "different_currency" in signals


def test_same_period_conflicting_values_defers_to_llm():
    """A real conflict must not be auto-labelled; a hidden qualifier may explain it."""
    t, _, _ = classify(fact("6.5%", "FY25"), fact("7.2%", "FY25"))
    assert t is None


def test_overlapping_periods_defer_to_llm():
    t, _, _ = classify(
        fact("₹2,076 Cr", "Q4 FY24", predicate="revenue"),
        fact("₹8,142 Cr", "FY24", predicate="revenue"),
    )
    assert t is None


def test_unknown_period_defers_to_llm():
    t, _, _ = classify(fact("6.5%", ""), fact("7.2%", "FY25"))
    assert t is None


def test_unitless_counts_defer_to_llm():
    """"18.8 msf" and "3,730 sq ft" both parse as bare counts, so the arithmetic
    cannot tell they are in different units. The model reads the units off the quotes."""
    t, _, signals = classify(
        fact("18.8", "as at 31 March 2024", predicate="logistics area under management"),
        fact("3,730", "as at 31 December 2023", predicate="logistics area under management"),
    )
    assert t is None
    assert "unitless counts" in signals


def test_currency_agreement_still_decides_by_rule():
    """The count guard must not disable the deterministic path for real units."""
    t, _, _ = classify(
        fact("₹8,142 Cr", "FY24", predicate="revenue"),
        fact("₹81,420 million", "FY24", predicate="revenue"),
    )
    assert t == "corroborates"


def test_non_numeric_facts_defer_to_llm():
    a = fact("", "as at 31 March 2022", predicate="director status")
    b = fact("", "as at 31 March 2024", predicate="director status")
    assert classify(a, b)[0] is None


def test_signals_are_reported_for_the_prompt():
    _, _, signals = classify(fact("6.5%", "FY25"), fact("7.2%", "FY25"))
    assert "periods:" in signals and "equal" in signals
