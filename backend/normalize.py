"""Pure normalization logic: quantities, currencies, and time periods.

Every reconciliation verdict depends on this module being correct, so it is
kept free of I/O and LLM calls and covered by tests/test_normalize.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date

# --- scale words -------------------------------------------------------------
# Indian numbering (lakh/crore) coexists with Western scales in the same corpus.
SCALES = {
    "hundred": 1e2,
    "thousand": 1e3,
    "k": 1e3,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "million": 1e6,
    "mn": 1e6,
    "mln": 1e6,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
    "billion": 1e9,
    "bn": 1e9,
    "trillion": 1e12,
    "tn": 1e12,
}

CURRENCIES = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ],
        start=1,
    )
}


@dataclass
class Quantity:
    """A numeric value normalized to a base unit.

    `value` is expressed in the base unit of `dimension`: absolute currency
    units for 'currency' (e.g. INR, not crore), fraction-free percent points
    for 'ratio' (6.5% -> 6.5), raw count otherwise.
    """

    value: float | None
    unit: str | None          # 'INR' | 'USD' | 'percent' | None
    dimension: str | None     # 'currency' | 'ratio' | 'count' | None
    raw: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Period:
    start: date | None
    end: date | None
    raw: str
    kind: str  # 'fy' | 'quarter' | 'half' | 'calendar' | 'month' | 'instant' | 'unknown'

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "raw": self.raw,
            "kind": self.kind,
        }


# --- quantities --------------------------------------------------------------

_NUM = r"[-+]?\d[\d,]*\.?\d*"


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _paren_sign(low: str) -> float:
    """-1 when the leading number is bracketed, as accounting notation for a loss.

    Only the *first* number counts, so a trailing footnote marker in
    "₹8,142 Cr (1)" does not flip the sign of the figure it annotates.
    """
    num = re.search(_NUM, low)
    if not num:
        return 1.0
    for grp in re.finditer(r"\([^()]*\)", low):
        if grp.start() < num.start() and num.end() <= grp.end():
            return -1.0
    return 1.0


def parse_quantity(raw: str, unit_hint: str | None = None) -> Quantity:
    """Parse a human-written quantity into a normalized Quantity.

    `unit_hint` carries table-header context such as "₹ in millions", which is
    where a large share of financial-table numbers get their scale from.
    """
    if raw is None:
        return Quantity(None, None, None, "")
    text = str(raw).strip()
    low = text.lower()
    sign = _paren_sign(low)

    # basis points before percent, so "650 bps" is not read as a bare count
    m = re.search(rf"({_NUM})\s*(?:bps|basis points?)", low)
    if m:
        v = _to_float(m.group(1))
        return Quantity(v / 100 * sign if v is not None else None, "percent", "ratio", text)

    # \b only after the word forms: a trailing '%' at end-of-string is not a word boundary
    m = re.search(
        rf"({_NUM})\s*(?:%|(?:per\s*cent|percent|percentage\s*points?|pp)\b)", low
    )
    if m:
        v = _to_float(m.group(1))
        return Quantity(v * sign if v is not None else None, "percent", "ratio", text)

    currency = None
    for token, code in CURRENCIES.items():
        if token in ("$", "₹", "€", "£"):
            if token in low:
                currency = code
                break
        elif re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", low):
            currency = code
            break

    scale = 1.0
    for word, mult in sorted(SCALES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){word}s?(?![a-z])", low):
            scale = mult
            break

    m = re.search(_NUM, low)
    value = _to_float(m.group(0)) if m else None
    if value is None:
        return Quantity(None, currency, "currency" if currency else None, text)
    value *= sign

    # Fall back to the hint only when the value itself carried no scale/currency.
    if unit_hint:
        hint = parse_unit_hint(unit_hint)
        if scale == 1.0 and hint.get("scale"):
            scale = hint["scale"]
        if currency is None and hint.get("currency"):
            currency = hint["currency"]

    value *= scale
    if currency:
        return Quantity(value, currency, "currency", text)
    return Quantity(value, None, "count", text)


def parse_unit_hint(hint: str) -> dict:
    """Pull currency/scale out of a table header like '(₹ in crore)'."""
    low = (hint or "").lower()
    out: dict = {}
    for token, code in CURRENCIES.items():
        if token in ("$", "₹", "€", "£"):
            if token in low:
                out["currency"] = code
                break
        elif re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", low):
            out["currency"] = code
            break
    for word, mult in sorted(SCALES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){word}s?(?![a-z])", low):
            out["scale"] = mult
            break
    return out


def values_comparable(a: Quantity, b: Quantity) -> tuple[bool, str]:
    """Can these two quantities be compared without inventing information?

    Cross-currency pairs deliberately return False: the correct FX rate is
    period-dependent, and silently picking one manufactures false precision.
    """
    if a.value is None or b.value is None:
        return False, "missing_value"
    if a.dimension != b.dimension:
        return False, "different_dimension"
    if a.dimension == "currency" and a.unit != b.unit:
        return False, "different_currency"
    return True, "comparable"


def values_agree(a: Quantity, b: Quantity, tol: float = 0.01) -> bool:
    """Relative-tolerance agreement, so rounding and restatement don't read as conflict."""
    ok, _ = values_comparable(a, b)
    if not ok:
        return False
    if a.value == b.value:
        return True
    denom = max(abs(a.value), abs(b.value))
    if denom == 0:
        return abs(a.value - b.value) < 1e-9
    return abs(a.value - b.value) / denom <= tol


# --- periods -----------------------------------------------------------------

def _fy_bounds(end_year: int, convention: str = "IN") -> tuple[date, date]:
    """Indian FY: FY24 == 1 Apr 2023 .. 31 Mar 2024."""
    if convention == "CY":
        return date(end_year, 1, 1), date(end_year, 12, 31)
    return date(end_year - 1, 4, 1), date(end_year, 3, 31)


def _norm_year(y: int) -> int:
    return y + 2000 if y < 100 else y


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _quarter_number(low: str) -> int | None:
    """Quarter index from 'Q3 FY25' or 'the third quarter of FY2025/26'."""
    m = re.search(r"\bq([1-4])\b|\bq([1-4])\s*fy", low)
    if m:
        return int(m.group(1) or m.group(2))
    m = re.search(r"\b(first|second|third|fourth)\s+quarter\b", low)
    return ORDINALS[m.group(1)] if m else None


def _fy_end_year(low: str) -> int | None:
    """Year a fiscal year ENDS in, accepting FY25, FY2024-25 and FY2024/25 alike."""
    m = re.search(r"(?:fy|fiscal(?:\s+year)?)?\s*'?(\d{4})\s*[-/]\s*'?(\d{2,4})", low)
    if m:
        start, tail = int(m.group(1)), m.group(2)
        end = int(str(start)[:2] + tail.zfill(2)) if len(tail) == 2 else int(tail)
        if end == start + 1:
            return end
    m = re.search(r"(?:fy|fiscal(?:\s+year)?)\s*'?(\d{2,4})\b", low)
    return _norm_year(int(m.group(1))) if m else None


def parse_period(raw: str, convention: str = "IN") -> Period:
    """Parse a period expression into concrete bounds.

    Handles the FY/quarter/calendar conventions that make Indian financial and
    government documents look contradictory when they are merely offset.
    """
    if not raw:
        return Period(None, None, "", "unknown")
    text = str(raw).strip()
    low = text.lower().replace("–", "-").replace("—", "-")

    # Q4 FY24 / Q4FY2024 / "the fourth quarter of FY2025/26"
    q = _quarter_number(low)
    if q:
        y = _fy_end_year(low)
        if y is None:
            return Period(None, None, text, "unknown")
        fy_start, _ = _fy_bounds(y, convention)
        sm = fy_start.month + 3 * (q - 1)
        sy = fy_start.year + (sm - 1) // 12
        sm = (sm - 1) % 12 + 1
        em = sm + 2
        ey = sy + (em - 1) // 12
        em = (em - 1) % 12 + 1
        last = _month_end(ey, em)
        return Period(date(sy, sm, 1), date(ey, em, last), text, "quarter")

    # H1/H2 FY25
    m = re.search(r"h([12])\s*[,\-]?\s*fy\s*'?(\d{2,4})", low)
    if m:
        h, y = int(m.group(1)), _norm_year(int(m.group(2)))
        fy_start, fy_end = _fy_bounds(y, convention)
        if h == 1:
            return Period(fy_start, date(fy_start.year, 9, 30), text, "half")
        return Period(date(fy_start.year, 10, 1), fy_end, text, "half")

    # FY2023-24 / FY 23-24 / fiscal year 2024-25
    m = re.search(r"(?:fy|fiscal(?:\s+year)?)\s*'?(\d{4})\s*[-/]\s*'?(\d{2,4})", low)
    if m:
        end = _norm_year(int(m.group(2)))
        if end < int(m.group(1)):  # "2023-24" -> end year 2024
            end = int(str(m.group(1))[:2] + m.group(2).zfill(2))
        return Period(*_fy_bounds(end, convention), raw=text, kind="fy")

    # FY24 / FY2024 / fiscal 2024
    m = re.search(r"(?:fy|fiscal(?:\s+year)?)\s*'?(\d{2,4})\b", low)
    if m:
        return Period(*_fy_bounds(_norm_year(int(m.group(1))), convention), raw=text, kind="fy")

    # bare 2023-24 / 2024-25 -> fiscal year by convention in Indian documents
    m = re.search(r"\b(\d{4})\s*[-/]\s*(\d{2,4})\b", low)
    if m:
        start_y = int(m.group(1))
        tail = m.group(2)
        end = int(str(start_y)[:2] + tail.zfill(2)) if len(tail) == 2 else int(tail)
        if 1900 < start_y < 2100 and end == start_y + 1:
            return Period(*_fy_bounds(end, convention), raw=text, kind="fy")

    # explicit instant: 31 March 2024 / March 31, 2024 / 31.03.2024
    inst = _parse_instant(low)
    if inst:
        return Period(inst, inst, text, "instant")

    # month-year: March 2024
    m = re.search(r"\b([a-z]{3,9})\s+(\d{4})\b", low)
    if m and m.group(1)[:3] in MONTHS:
        mm, yy = MONTHS[m.group(1)[:3]], int(m.group(2))
        return Period(date(yy, mm, 1), date(yy, mm, _month_end(yy, mm)), text, "month")

    # calendar year: CY2024 / calendar year 2024 / bare 2024
    m = re.search(r"(?:cy|calendar\s+year)\s*'?(\d{2,4})\b", low)
    if m:
        y = _norm_year(int(m.group(1)))
        return Period(date(y, 1, 1), date(y, 12, 31), text, "calendar")
    m = re.search(r"\b(19|20)(\d{2})\b", low)
    if m:
        y = int(m.group(0))
        return Period(date(y, 1, 1), date(y, 12, 31), text, "calendar")

    return Period(None, None, text, "unknown")


def _month_end(year: int, month: int) -> int:
    if month == 12:
        return 31
    nxt = date(year + (month // 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days


def _parse_instant(low: str) -> date | None:
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", low)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return date(y, mo, d)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})\b", low)
    if m and m.group(2)[:3] in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", low)
    if m and m.group(1)[:3] in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
    return None


def periods_relation(a: Period, b: Period) -> str:
    """'equal' | 'overlap' | 'disjoint' | 'unknown'."""
    if not a or not b or a.start is None or b.start is None:
        return "unknown"
    ae, be = a.end or a.start, b.end or b.start
    if a.start == b.start and ae == be:
        return "equal"
    if a.start <= be and b.start <= ae:
        return "overlap"
    return "disjoint"
