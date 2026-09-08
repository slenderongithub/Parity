"""Ingestion pipeline: PDF in, grounded facts and cross-document relations out.

Written as a synchronous generator of events so the API can stream progress while
it runs, and so it can equally be driven from a script.
"""

from __future__ import annotations

import json
import os

import db
import extract
import ingest
import llm
import match
from normalize import parse_period, parse_quantity

MAX_PAGES = int(os.getenv("FKL_MAX_PAGES", "0")) or None      # 0 = whole document
PAGES_PER_CHUNK = int(os.getenv("FKL_PAGES_PER_CHUNK", "10"))
FACTS_PER_CHUNK = int(os.getenv("FKL_FACTS_PER_CHUNK", "25"))
TOP_K = int(os.getenv("FKL_TOP_K", "5"))                      # candidates considered per fact
MAX_ADJUDICATIONS = int(os.getenv("FKL_MAX_ADJUDICATIONS", "60"))  # LLM budget per upload


def _dedupe_key(f: dict) -> tuple:
    return (f.get("claim_key", ""), f.get("value_raw", ""), f.get("period_raw", ""))


def _short_error(e: Exception) -> str:
    """A one-line, human-readable summary of an LLM call failure.

    The Gemini SDK's exception text is a multi-hundred-character dump of the raw API
    error body (nested quota/retry JSON) - useful in server logs, not in the UI.
    """
    msg = str(e)
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "quota" in msg.lower():
        return "Gemini free-tier quota exhausted for the day"
    return msg.splitlines()[0][:160]


def process_pdf(conn, path: str, filename: str):
    """Yield event dicts: doc, fact, relation, note, done."""
    pages = ingest.extract_pages(path, max_pages=MAX_PAGES)
    if not pages:
        yield {"event": "error", "message": "No extractable text (scanned or image-only PDF?)"}
        return
    page_map = ingest.page_text_map(pages)

    ctx = extract.doc_context(ingest.doc_head(pages)) if llm.available() else {
        "title": filename, "publisher": "", "doc_date": "",
        "primary_entity": "", "fy_convention": "IN", "currency_hint": "",
    }
    doc_id = db.insert_document(
        conn, filename=filename, page_count=len(pages),
        title=ctx.get("title") or filename, publisher=ctx.get("publisher"),
        doc_date=ctx.get("doc_date"), primary_entity=ctx.get("primary_entity"),
        fy_convention=ctx.get("fy_convention") or "IN", currency_hint=ctx.get("currency_hint"),
    )
    yield {"event": "doc", "doc_id": doc_id, "filename": filename,
           "pages": len(pages), **{k: ctx.get(k) for k in
                                   ("title", "publisher", "doc_date", "primary_entity",
                                    "fy_convention")}}

    if not llm.available():
        yield {"event": "error",
               "message": "GEMINI_API_KEY not set - cannot extract facts. "
                          "Browse the pre-built data/facts.db instead."}
        return

    chunks = ingest.chunk_pages(pages, PAGES_PER_CHUNK)
    yield {"event": "plan", "chunks": len(chunks), "pages": len(pages)}

    seen: set[tuple] = set()
    fy = ctx.get("fy_convention") or "IN"
    adjudications = 0
    budget_warned = False
    totals = {"facts": 0, "grounded": 0, "ungrounded": 0, "relations": 0, "unexamined": 0}

    for ch in chunks:
        try:
            raw = extract.extract_chunk(ch.text, ctx, cap=FACTS_PER_CHUNK)
        except Exception as e:  # noqa: BLE001 - one bad chunk must not kill the upload
            yield {"event": "note", "level": "warn",
                   "message": f"Extraction failed on pages {ch.start_page}-{ch.end_page}: "
                              f"{_short_error(e)}"}
            continue

        raw = extract.ground_facts(raw, page_map, range(ch.start_page, ch.end_page + 1))

        new_rows, new_keys = [], []
        for f in raw:
            f["claim_key"] = extract.claim_key(f)
            if not f["claim_key"] or not f.get("quote"):
                continue
            k = _dedupe_key(f)
            if k in seen:
                continue
            seen.add(k)

            q = parse_quantity(f.get("value_raw", ""), f.get("unit_hint"))
            p = parse_period(f.get("period_raw", ""), convention=fy)
            verified = bool(f.get("quote_verified"))
            totals["facts"] += 1
            totals["grounded" if verified else "ungrounded"] += 1

            fact_id = db.insert_fact(
                conn, doc_id=doc_id, page=f.get("page"), fact_type=f.get("fact_type"),
                subject=f.get("subject"), predicate=f.get("predicate"),
                qualifiers=json.dumps(f.get("qualifiers") or []),
                claim_key=f["claim_key"], value_raw=f.get("value_raw"),
                value_num=q.value, unit=q.unit, dimension=q.dimension,
                period_raw=f.get("period_raw"),
                period_start=p.start.isoformat() if p.start else None,
                period_end=p.end.isoformat() if p.end else None,
                period_kind=p.kind, source_quote=f.get("quote"),
                quote_verified=int(verified), quote_score=f.get("quote_score"),
                grounding=f.get("grounding"),
                confidence=f.get("confidence"), embedding=None,
            )
            yield {"event": "fact", "id": fact_id, "doc_id": doc_id,
                   "page": f.get("page"), "subject": f.get("subject"),
                   "predicate": f.get("predicate"), "value_raw": f.get("value_raw"),
                   "period_raw": f.get("period_raw"), "fact_type": f.get("fact_type"),
                   "quote": f.get("quote"), "quote_verified": verified,
                   "grounding": f.get("grounding"), "quote_score": f.get("quote_score")}

            if verified:
                new_rows.append(fact_id)
                new_keys.append(f["claim_key"])

        if not new_rows:
            continue

        vecs = match.embed(new_keys)
        for fid, v in zip(new_rows, vecs):
            conn.execute("UPDATE facts SET embedding=? WHERE id=?", [match.to_blob(v), fid])
        conn.commit()

        rows = [db.fact_row(conn, fid) for fid in new_rows]
        existing = db.verified_facts(conn, exclude_doc_id=doc_id)
        if not existing:
            continue

        for a, b, verdict in _limited_relate(rows, vecs, existing, adjudications):
            if verdict is None:
                totals["unexamined"] += 1
                continue
            if verdict["decided_by"] == "llm":
                adjudications += 1
            rid = db.insert_relation(
                conn, fact_a_id=a["id"], fact_b_id=b["id"],
                relation_type=verdict["relation_type"], decided_by=verdict["decided_by"],
                explanation=verdict.get("explanation"), similarity=verdict.get("similarity"),
                confidence=verdict.get("confidence"),
            )
            if rid is None:
                continue
            totals["relations"] += 1
            yield {"event": "relation", "id": rid,
                   "type": verdict["relation_type"], "decided_by": verdict["decided_by"],
                   "explanation": verdict.get("explanation"),
                   "similarity": round(verdict.get("similarity") or 0, 3),
                   "a": db.row_to_dict(a), "b": db.row_to_dict(b)}

        if adjudications >= MAX_ADJUDICATIONS and not budget_warned:
            budget_warned = True
            yield {"event": "note", "level": "warn",
                   "message": f"Reached the LLM adjudication budget ({MAX_ADJUDICATIONS}) for "
                              "this document; further ambiguous pairs were left unexamined "
                              "rather than guessed. Raise FKL_MAX_ADJUDICATIONS to go deeper."}

    if totals["facts"] == 0:
        # Every chunk failed (quota exhausted, or an unreadable PDF). The document row
        # was written before extraction began, so remove it rather than leave an empty
        # document in the corpus that looks successfully ingested.
        conn.execute("DELETE FROM documents WHERE id=?", [doc_id])
        conn.commit()
        yield {"event": "error", "doc_id": doc_id,
               "message": "No facts could be extracted, so this document was not kept. "
                          "The most common cause is an exhausted API quota — check the "
                          "warnings above and retry."}
        return

    yield {"event": "done", "doc_id": doc_id, "totals": totals, "counts": db.counts(conn)}


def _limited_relate(rows, vecs, existing, already: int):
    """Yield (fact_a, fact_b, verdict) for candidate pairs above the similarity floor.

    Bounded twice over: at most TOP_K comparisons per new fact, and a global
    adjudication budget, so a large corpus cannot trigger a call explosion.
    """
    sims = match.candidates(vecs, existing)
    if sims.shape[1] == 0:
        return

    # Collect every candidate pair first, then work through them best-match-first.
    # The adjudication budget is scarce, so it must be spent on the most likely
    # matches in the whole document rather than on whichever facts happen to be
    # processed first.
    pairs = []
    for i, a in enumerate(rows):
        taken = 0
        for j in sims[i].argsort()[::-1]:
            if taken >= TOP_K or sims[i][j] < match.SIM_THRESHOLD:
                break
            if existing[j]["doc_id"] == a["doc_id"]:
                continue
            taken += 1
            pairs.append((float(sims[i][j]), i, int(j)))
    pairs.sort(key=lambda p: -p[0])

    spent = already
    for sim, i, j in pairs:
        a, b = rows[i], existing[j]
        rtype, expl, signals = match.classify(a, b)

        # A deterministic verdict presumes the two claims really are about the same
        # thing. Only a strong claim-key match justifies that presumption; weaker
        # matches go to the model, which can answer "unrelated".
        if sim < match.RULE_SIM:
            rtype = None
            signals += (
                f"\n- claim-key similarity: {sim:.2f} (moderate: verify these are the "
                "same claim before relating them)"
            )

        if rtype:
            yield a, b, {"relation_type": rtype, "decided_by": "rule",
                         "explanation": expl, "confidence": 0.95, "similarity": sim}
        elif spent < MAX_ADJUDICATIONS:
            spent += 1
            try:
                v = match.adjudicate(a, b, signals)
            except Exception as e:  # noqa: BLE001 - degrade, never abort ingestion
                yield a, b, {"relation_type": "needs_review", "decided_by": "rule",
                             "explanation": f"Adjudication call failed: {_short_error(e)}",
                             "confidence": 0.0, "similarity": sim}
                continue
            if v.get("relation_type") == "unrelated":
                continue
            yield a, b, {"relation_type": v["relation_type"], "decided_by": "llm",
                         "explanation": v.get("explanation", ""),
                         "confidence": float(v.get("confidence", 0.5)), "similarity": sim}
        else:
            # Budget exhausted. Deliberately yield nothing: a pair the system never
            # examined is not a relation, and recording one would assert a finding that
            # no rule and no model ever made. It is counted instead.
            yield a, b, None
