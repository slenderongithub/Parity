"""Rebuild every cross-document relation from facts already in the database.

Extraction is the expensive half of this system (an LLM call per chunk, rate limited);
the comparison logic is the half that actually changes during development. This
recomputes all relations from stored facts and embeddings without re-reading a single
PDF, which is also the cheapest way to see the effect of a rule change on the whole
corpus.

    python backend/rematch.py

Documents are replayed in ingestion order so each one is compared only against those
before it — the same pairs the incremental pipeline would have produced.
"""

from __future__ import annotations

import numpy as np

import config  # noqa: F401  - loads .env
import db
import match
import pipeline


def main() -> int:
    conn = db.connect()
    before = conn.execute("SELECT count(*) FROM relations").fetchone()[0]
    conn.execute("DELETE FROM relations")
    conn.commit()
    print(f"cleared {before} relations; recomputing from stored facts\n", flush=True)

    doc_ids = [r["id"] for r in conn.execute("SELECT id FROM documents ORDER BY id")]
    totals: dict[str, int] = {}
    unexamined = 0

    for doc_id in doc_ids:
        rows = conn.execute(
            """SELECT f.*, d.filename, d.title, d.publisher, d.doc_date
               FROM facts f JOIN documents d ON d.id = f.doc_id
               WHERE f.doc_id = ? AND f.quote_verified = 1 AND f.embedding IS NOT NULL""",
            [doc_id],
        ).fetchall()
        earlier = conn.execute(
            """SELECT f.*, d.filename, d.title, d.publisher, d.doc_date
               FROM facts f JOIN documents d ON d.id = f.doc_id
               WHERE f.doc_id < ? AND f.quote_verified = 1 AND f.embedding IS NOT NULL""",
            [doc_id],
        ).fetchall()
        name = conn.execute("SELECT filename FROM documents WHERE id=?", [doc_id]).fetchone()[0]
        if not rows or not earlier:
            print(f"=== {name}: {len(rows)} facts, nothing earlier to compare against", flush=True)
            continue

        vecs = np.vstack([match.from_blob(r["embedding"]) for r in rows])
        print(f"=== {name}: {len(rows)} facts vs {len(earlier)} earlier facts", flush=True)

        spent, made = 0, {}
        for a, b, verdict in pipeline._limited_relate(rows, vecs, earlier, 0):
            if verdict is None:
                unexamined += 1
                continue
            if verdict["decided_by"] == "llm":
                spent += 1
            if db.insert_relation(
                conn, fact_a_id=a["id"], fact_b_id=b["id"],
                relation_type=verdict["relation_type"], decided_by=verdict["decided_by"],
                explanation=verdict.get("explanation"), similarity=verdict.get("similarity"),
                confidence=verdict.get("confidence"),
            ) is None:
                continue
            key = f"{verdict['relation_type']}/{verdict['decided_by']}"
            made[key] = made.get(key, 0) + 1
            totals[key] = totals.get(key, 0) + 1
            if verdict["relation_type"] in ("corroborates", "contradicts"):
                print(f"    {verdict['relation_type']:14} [{verdict['decided_by']}] "
                      f"{(verdict.get('explanation') or '')[:100]}", flush=True)
        print(f"    -> {made or 'none'} ({spent} model calls)\n", flush=True)

    print(f"TOTALS: {totals}")
    print(f"unexamined pairs (budget): {unexamined}")
    print(db.counts(conn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
