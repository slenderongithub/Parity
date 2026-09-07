"""Cross-document comparison.

Two-stage by design:
  1. Local embeddings over the CLAIM KEY find facts that talk about the same thing,
     at zero API cost, so adding a document does not cost N LLM calls.
  2. Deterministic rules settle every pair whose period/unit/value arithmetic is
     unambiguous. Only genuinely ambiguous pairs reach the model, and it is asked a
     narrow question with both quotes in hand.

`decided_by` on each relation records which stage decided it.
"""

from __future__ import annotations

import json
import threading

import numpy as np
from pydantic import BaseModel, Field

import llm
from normalize import Period, Quantity, periods_relation, values_agree, values_comparable

SIM_THRESHOLD = 0.78     # below this, two claim keys are not worth comparing at all
RULE_SIM = 0.88          # above this, a claim-key match is strong enough to trust rules on
TOLERANCE = 0.01         # 1%: rounding and restatement should not read as conflict

_model = None
_model_lock = threading.Lock()


def embedder():
    """Lazy singleton: loading the ONNX model costs ~1s and is not needed keyless."""
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    vecs = np.array(list(embedder().embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


# --- adjudication schema -----------------------------------------------------


class Verdict(BaseModel):
    relation_type: str = Field(
        description="one of: corroborates, contradicts, reconciled_time, "
        "reconciled_units, reconciled_scope, unrelated, needs_review"
    )
    explanation: str = Field(description="one or two sentences naming the deciding attribute")
    confidence: float


ADJUDICATE_PROMPT = """Two claims were extracted from different documents. Decide how they relate.

CLAIM A  (from {doc_a}, page {page_a})
  subject:    {subj_a}
  predicate:  {pred_a}
  qualifiers: {qual_a}
  value:      {val_a}
  period:     {per_a}
  evidence:   "{quote_a}"

CLAIM B  (from {doc_b}, page {page_b})
  subject:    {subj_b}
  predicate:  {pred_b}
  qualifiers: {qual_b}
  value:      {val_b}
  period:     {per_b}
  evidence:   "{quote_b}"

DETERMINISTIC CHECKS ALREADY PERFORMED
{signals}

CHOOSE ONE relation_type:
- corroborates    — same claim, consistent, even if worded or formatted differently.
- contradicts     — same subject, same period, same basis, but the values genuinely
                    conflict. Only choose this if you cannot find a difference in
                    scope, basis, measure or period that would explain the gap.
- reconciled_time  — differ only because they cover different periods or as-of dates.
- reconciled_units — differ only because of currency, scale or unit of measure.
- reconciled_scope — differ only because of what is being measured or included
                     (consolidated vs standalone, segment vs total, adjusted vs
                     reported, revenue from services vs revenue from operations,
                     provisional vs revised).
- unrelated        — not actually claims about the same thing.
- needs_review     — the evidence is too incomplete to decide.

DECIDE IN THIS ORDER:
1. Are these the SAME underlying claim? If they measure different quantities — different
   metrics, different subjects, or different units of measure (e.g. floor area vs a count
   of buildings) — answer "unrelated". The reconciliation labels are ONLY for one claim
   expressed two ways. Do not use "reconciled_time" as a catch-all for two unlike numbers
   that happen to carry different dates.
2. If they are the same claim, is the difference explained by period, units or scope?
   Then use the matching reconciliation label.
3. Only if they are the same claim, on the same basis and period, and still disagree,
   answer "contradicts". An unexplained conflict IS a contradiction — do not invent a
   reconciliation the evidence does not show.

"contradicts" is the strongest claim this system makes, so it carries a burden of proof:
never choose it unless the evidence establishes that both figures cover the SAME period
on the SAME basis. If either period is vague, relative or unstated — "the same period
last year", "a year ago", "the period under review" — you cannot establish that, so
answer "needs_review" and say which period could not be pinned down.

The explanation must name the specific attribute that decided it, and cite the values.
Write it so it stands on its own: refer to the figures and their sources (for example
"the 6.4% first advance estimate vs the 6.5% second advance estimate"), never as
"Claim A" or "Claim B" — a reader sees the two facts side by side without those labels.
"""


def _fmt_qual(q) -> str:
    if not q:
        return "none"
    if isinstance(q, str):
        try:
            q = json.loads(q)
        except ValueError:
            return q
    if q and isinstance(q[0], dict):
        return ", ".join(f"{i.get('key')}={i.get('value')}" for i in q) or "none"
    return ", ".join(map(str, q)) or "none"


def _as_quantity(row) -> Quantity:
    return Quantity(
        value=row["value_num"],
        unit=row["unit"],
        dimension=row["dimension"],
        raw=row["value_raw"] or "",
    )


def _as_period(row) -> Period:
    from datetime import date

    def d(s):
        return date.fromisoformat(s) if s else None

    return Period(d(row["period_start"]), d(row["period_end"]),
                  row["period_raw"] or "", row["period_kind"] or "unknown")


def classify(a, b) -> tuple[str | None, str, str]:
    """Deterministic verdict for a candidate pair.

    Returns (relation_type|None, explanation, signals). A None type means the
    arithmetic was not decisive and the pair must go to the model.
    """
    qa, qb = _as_quantity(a), _as_quantity(b)
    pa, pb = _as_period(a), _as_period(b)
    prel = periods_relation(pa, pb)
    comparable, why = values_comparable(qa, qb)

    signals = (
        f"- periods: {pa.raw or '?'} vs {pb.raw or '?'} -> {prel}\n"
        f"- values comparable: {comparable} ({why})\n"
        f"- normalized values: {qa.value} {qa.unit or ''} vs {qb.value} {qb.unit or ''}"
    )

    # Non-numeric claims have no arithmetic to settle; the model reads the evidence.
    if qa.value is None or qb.value is None:
        return None, "", signals

    if prel == "unknown":
        return None, "", signals

    # Bare counts carry no parsed unit, so "18.8 msf" and "3,730 thousand sq ft" look
    # equally comparable to the arithmetic. Only currencies and ratios are normalized
    # well enough to decide on; everything else goes to the model, which can read the
    # units off the quotes.
    if qa.dimension not in ("currency", "ratio") or qb.dimension not in ("currency", "ratio"):
        signals += "\n- both values are unitless counts: units not verified by normalization"
        return None, "", signals

    if not comparable:
        if why == "different_currency":
            signals += (
                f"\n- different currencies ({qa.unit} vs {qb.unit}): not directly comparable "
                "without a period-appropriate exchange rate. Do not convert; if these are the "
                "same claim, the relation is reconciled_units."
            )
        return None, "", signals

    # Only ONE deterministic verdict is safe, and only because it is self-validating:
    # two values that AGREE numerically for the SAME period are almost certainly the
    # same claim — the agreement is itself the evidence.
    #
    # The tempting mirror rule ("values differ over disjoint periods -> reconciled by
    # time") is not safe, and was removed after it fired on pairs like
    # "Total equity 48,465.92 (FY21)" vs "Total Assets 112,134.30 (FY23)". Differing
    # values across different dates is trivially true of ANY two unrelated metrics, so
    # the rule asserts a relationship it has no evidence for. Those pairs now go to the
    # model, which can answer "unrelated".
    if prel == "equal" and values_agree(qa, qb, TOLERANCE):
        return (
            "corroborates",
            f"Both report {qa.raw} and {qb.raw} for the same period "
            f"({pa.raw or 'same period'}); values agree within {TOLERANCE:.0%}.",
            signals,
        )
    return None, "", signals


def adjudicate(a, b, signals: str) -> dict:
    """Ask the model for a narrow judgement with both quotes in hand."""
    prompt = ADJUDICATE_PROMPT.format(
        doc_a=a["title"] or a["filename"], page_a=a["page"],
        subj_a=a["subject"], pred_a=a["predicate"], qual_a=_fmt_qual(a["qualifiers"]),
        val_a=a["value_raw"] or "(non-numeric)", per_a=a["period_raw"] or "(not stated)",
        quote_a=(a["source_quote"] or "").replace('"', "'"),
        doc_b=b["title"] or b["filename"], page_b=b["page"],
        subj_b=b["subject"], pred_b=b["predicate"], qual_b=_fmt_qual(b["qualifiers"]),
        val_b=b["value_raw"] or "(non-numeric)", per_b=b["period_raw"] or "(not stated)",
        quote_b=(b["source_quote"] or "").replace('"', "'"),
        signals=signals,
    )
    v = llm.generate_json(prompt, Verdict)
    allowed = {
        "corroborates", "contradicts", "reconciled_time", "reconciled_units",
        "reconciled_scope", "unrelated", "needs_review",
    }
    if v.get("relation_type") not in allowed:
        v["relation_type"] = "needs_review"
    return v


def candidates(new_vecs: np.ndarray, existing: list, threshold: float = SIM_THRESHOLD):
    """Cosine similarity of claim keys, new facts against everything already stored.

    One matrix multiply, no API calls — this is what keeps incremental ingestion
    cheap as the corpus grows.
    """
    if not existing or new_vecs.shape[0] == 0:
        return np.zeros((new_vecs.shape[0], 0), dtype=np.float32)
    mat = np.vstack([from_blob(r["embedding"]) for r in existing])
    return new_vecs @ mat.T
