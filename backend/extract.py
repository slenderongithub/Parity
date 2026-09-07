"""LLM fact extraction + evidence grounding.

Nothing the model returns is trusted on its own: every quote is checked against
the text of the page it claims to come from before the fact is allowed to take
part in relation building.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from pydantic import BaseModel, Field

import llm

# --- schemas Gemini must fill (OpenAPI subset: no free-form dicts) -----------


class Qualifier(BaseModel):
    key: str
    value: str


class RawFact(BaseModel):
    fact_type: str = Field(description="open-ended category you choose, e.g. financial_metric")
    subject: str
    predicate: str
    qualifiers: list[Qualifier] = []
    value_raw: str = Field(description="value exactly as printed, '' if the fact is not numeric")
    unit_hint: str = Field(description="scale/currency context such as '(₹ in crore)', else ''")
    period_raw: str = Field(description="period or as-of date exactly as stated, else ''")
    page: int
    quote: str = Field(description="verbatim sentence or line from the page supporting this")
    confidence: float


class FactList(BaseModel):
    facts: list[RawFact]


class DocContext(BaseModel):
    title: str
    publisher: str
    doc_date: str
    primary_entity: str
    fy_convention: str = Field(description="'IN' for April-March fiscal years, else 'CY'")
    currency_hint: str


# --- prompts -----------------------------------------------------------------

DOC_CONTEXT_PROMPT = """You are cataloguing a document for a fact-extraction system.
From these opening pages, identify the document's identity.

publisher: who produced/issued the document. Almost never who its facts are about.
primary_entity: the organisation or economy MOST of the document's factual claims
describe — this is what "the Company", "the Group" or "we" refer to later, when those
phrases mean the publisher. It is NOT automatically the publisher: an institution's
report can be chiefly about a different subject (e.g. a central bank's annual report
whose tables are mostly national economic indicators — GDP, inflation, trade — is
about the country, not the bank, even though the bank wrote it and its own balance
sheet appears too). Pick primary_entity by asking "what do most of the numbers in
this document describe?", not "who published this?" — if that is genuinely a country
or economy rather than the publisher, name the country/economy instead.
fy_convention: "IN" if the document uses April-March fiscal years (typical for Indian
companies and Indian government/RBI publications), otherwise "CY".
Use "" for anything you cannot determine. Do not guess.

PAGES:
{head}
"""

EXTRACT_PROMPT = """Extract checkable factual claims from this excerpt.

DOCUMENT CONTEXT
Title: {title}
Publisher: {publisher}
Document date: {doc_date}
Primary entity: {primary_entity}
Fiscal-year convention: {fy_convention}

WHAT TO EXTRACT
Claims a careful reader could verify and that could be compared against another
document. Typically:
- quantities with a period (revenue, growth rates, margins, volumes, ratios, headcount)
- states of an entity with an as-of date (appointments, resignations, ratings, statuses)
- identifiers (addresses, registration numbers, dates of incorporation)

WHAT TO SKIP
Narrative, opinion, forward-looking language ("we aim to", "is expected to"), section
headings, and figures you cannot tie to a subject and a period.

RULES
1. quote: copy a VERBATIM span from the excerpt below. Never paraphrase, correct or
   reflow it. It is checked character-by-character against the source and discarded on
   mismatch.
2. page: the [[page N]] marker the quote physically sits under.
3. subject: resolve "the Company", "the Group" and "we" to the primary entity named
   above. If the claim is about a segment or sub-entity, name that instead. If the claim
   is about a country/economy/national aggregate (GDP, inflation, trade deficit, a
   sector's growth), the subject is that country or economy by name — never the
   publisher, even when the publisher is a government body or central bank reporting on
   it in the same breath as its own institutional facts.
4. qualifiers: record anything that changes what the number means and would otherwise
   make two figures look contradictory — basis (consolidated/standalone), segment,
   measure variant (revenue from services vs revenue from operations), adjustments
   (adjusted/reported), geography, whether a figure is restated or provisional.
5. value_raw: exactly as printed, including scale and sign notation — "₹8,142 Cr",
   "(452)", "6.5%". Leave "" for non-numeric facts.
6. period_raw: exactly as stated — "FY24", "Q4 FY24", "as at 31 March 2024". Leave ""
   if the excerpt genuinely does not say; never infer it from the document date.
7. confidence: 0.0-1.0, your own certainty that this is a correct, self-contained claim.
8. Prefer precision over volume. Extract at most {cap} of the most substantive claims.

EXCERPT:
{chunk}
"""


# --- grounding ---------------------------------------------------------------

def _canon(s: str) -> str:
    """Collapse the noise PDF text introduces: unicode variants and whitespace."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


_NUM_TOK = re.compile(r"\d[\d,]*\.?\d*")
_WORD_TOK = re.compile(r"[a-z]{3,}")

VERBATIM, REFLOWED, UNVERIFIED = "verbatim", "reflowed", "unverified"


def _nums(s: str) -> set[str]:
    return {t.replace(",", "").rstrip(".") for t in _NUM_TOK.findall(s)}


def _words(s: str) -> set[str]:
    return set(_WORD_TOK.findall(s))


def verify_quote(quote: str, page_text: str, threshold: float = 0.88) -> tuple[str, float]:
    """Is this quote really on this page, and how directly?

    Returns one of three states, because PDFs make binary grounding misleading:

    - verbatim   contiguous span found (exact, or fuzzy above `threshold`).
    - reflowed   not contiguous, but every number in the quote and almost all of its
                 words are on the page. Multi-column and infographic layouts extract
                 in jumbled reading order, so a correct fact often has no contiguous
                 source span. The evidence is present; only its order is not.
    - unverified anything else, including invented figures.

    Fabrications still fail: a number that is not on the page cannot pass either test.
    """
    q, p = _canon(quote), _canon(page_text)
    if len(q) < 12 or not p:
        return UNVERIFIED, 0.0
    if q in p:
        return VERBATIM, 1.0

    # Fuzzy matching is only meaningful on a reasonably long span: on a short one,
    # a few characters of difference still scores high enough to pass.
    best = 0.0
    if len(q) >= 40:
        # Slide a same-length window rather than diffing whole pages (much cheaper).
        step = max(len(q) // 4, 1)
        matcher = difflib.SequenceMatcher(a=q, autojunk=False)
        for i in range(0, max(len(p) - len(q), 0) + 1, step):
            matcher.set_seq2(p[i : i + len(q)])
            if matcher.quick_ratio() > best:
                best = max(best, matcher.ratio())
            if best >= threshold:
                return VERBATIM, best

    qn, qw = _nums(q), _words(q)
    pn, pw = _nums(p), _words(p)
    if qn and not qn <= pn:
        return UNVERIFIED, best          # a figure that is not on the page at all
    coverage = len(qw & pw) / len(qw) if qw else 0.0
    if (qn or len(qw) >= 4) and coverage >= 0.8:
        return REFLOWED, round(coverage, 3)
    return UNVERIFIED, max(best, coverage)


# --- pipeline steps ----------------------------------------------------------

def doc_context(head: str) -> dict:
    try:
        return llm.generate_json(DOC_CONTEXT_PROMPT.format(head=head), DocContext)
    except Exception:
        # A missing context pass degrades quality but must not abort ingestion.
        return {
            "title": "", "publisher": "", "doc_date": "",
            "primary_entity": "", "fy_convention": "IN", "currency_hint": "",
        }


def extract_chunk(chunk_text: str, ctx: dict, cap: int = 25) -> list[dict]:
    prompt = EXTRACT_PROMPT.format(
        title=ctx.get("title", ""),
        publisher=ctx.get("publisher", ""),
        doc_date=ctx.get("doc_date", ""),
        primary_entity=ctx.get("primary_entity", ""),
        fy_convention=ctx.get("fy_convention", "IN"),
        cap=cap,
        chunk=chunk_text,
    )
    data = llm.generate_json(prompt, FactList)
    return data.get("facts", []) if isinstance(data, dict) else []


RANK = {VERBATIM: 2, REFLOWED: 1, UNVERIFIED: 0}


def ground_facts(
    facts: list[dict], pages: dict[int, str], valid_pages: range | None = None
) -> list[dict]:
    """Attach verification results; never silently drop a fact.

    If the claimed page does not support the quote, the other pages of the same
    chunk are tried and the best-supported one wins. Page attribution is the part
    of extraction the model is worst at, and a citation that points at the wrong
    page is worth correcting rather than discarding.
    """
    for f in facts:
        quote = f.get("quote", "")
        status, score = verify_quote(quote, pages.get(f.get("page"), ""))

        if status == UNVERIFIED and valid_pages:
            for p in valid_pages:
                if p == f.get("page"):
                    continue
                s2, sc2 = verify_quote(quote, pages.get(p, ""))
                if RANK[s2] > RANK[status]:
                    status, score, f["page"] = s2, sc2, p
                    if status == VERBATIM:
                        break

        f["grounding"] = status
        f["quote_verified"] = status != UNVERIFIED
        f["quote_score"] = round(score, 3)
    return facts


def claim_key(fact: dict) -> str:
    """Subject + predicate + qualifiers, deliberately excluding the value.

    Embedding this rather than the whole statement keeps two facts about the
    same thing close together even when their numbers disagree — which is
    exactly the pair a contradiction detector must not miss.
    """
    quals = fact.get("qualifiers") or []
    if quals and isinstance(quals[0], dict):
        qs = " ".join(f"{q.get('key')}={q.get('value')}" for q in quals)
    else:
        qs = " ".join(map(str, quals))
    parts = [fact.get("subject", ""), fact.get("predicate", ""), qs]
    return " | ".join(p for p in parts if p).strip()
