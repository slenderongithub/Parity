"""Print the four required cases from the knowledge layer, with evidence.

Reads whatever is in the database — nothing here is hard-coded to a document.

    python backend/report_cases.py [--limit 2]
"""

from __future__ import annotations

import sys

import config  # noqa: F401  - loads .env
import db

CASES = [
    ("1. CORROBORATED ACROSS DOCUMENTS", ["corroborates"]),
    ("2. CONTRADICTION", ["contradicts"]),
    ("3. RECONCILED BY CONTEXT", ["reconciled_time", "reconciled_units", "reconciled_scope"]),
    ("4. UNRESOLVED / NEEDS REVIEW", ["needs_review"]),
]


def show(conn, rel) -> None:
    a, b = db.row_to_dict(db.fact_row(conn, rel["fact_a_id"])), db.row_to_dict(
        db.fact_row(conn, rel["fact_b_id"])
    )
    print(f"\n  [{rel['relation_type']}] decided_by={rel['decided_by']} "
          f"sim={rel['similarity']:.3f} conf={rel['confidence']}")
    for tag, f in (("A", a), ("B", b)):
        print(f"    {tag}: {f.get('subject')} · {f.get('predicate')}"
              f" = {f.get('value_raw') or '(non-numeric)'}"
              f"  [{f.get('period_raw') or 'no period'}]")
        print(f"       source: {f.get('title') or f.get('filename')} p.{f.get('page')}"
              f"  ({f.get('grounding')})")
        print(f"       quote : \"{(f.get('source_quote') or '')[:150]}\"")
    print(f"    reasoning: {rel['explanation']}")


def main(limit: int = 2) -> int:
    conn = db.connect()
    print("DATABASE:", db.counts(conn))

    for title, types in CASES:
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)
        rows = conn.execute(
            f"SELECT * FROM relations WHERE relation_type IN "
            f"({','.join('?' * len(types))}) "
            f"ORDER BY (decided_by='llm') DESC, confidence DESC LIMIT ?",
            [*types, limit],
        ).fetchall()
        if not rows:
            print("  (none found)")
        for r in rows:
            show(conn, r)

    print("\n" + "=" * 78)
    print("EXTRACTION FAILURES CAUGHT BY THE GROUNDING VERIFIER")
    print("=" * 78)
    for r in conn.execute(
        """SELECT f.*, d.filename FROM facts f JOIN documents d ON d.id=f.doc_id
           WHERE f.quote_verified=0 ORDER BY f.quote_score DESC LIMIT ?""", [limit + 1]
    ):
        print(f"\n  {r['subject']} · {r['predicate']} = {r['value_raw']}"
              f"  [{r['filename']} p.{r['page']}, score {r['quote_score']}]")
        print(f'    rejected quote: "{(r["source_quote"] or "")[:150]}"')

    print("\n" + "=" * 78)
    print("REASONING FAILURE: ENTITY RESOLUTION CONFLATES PUBLISHER WITH SUBJECT")
    print("=" * 78)
    print("  A fact's subject equals its own document's publisher, yet still got linked to a")
    print("  fact from another document whose subject is a *different* string. The match only")
    print("  worked because embedding similarity on subject+predicate was forgiving -- identity")
    print("  did no real work. Detected generically: no filename or entity name is hard-coded.")
    rows = conn.execute(
        """SELECT r.*, fa.subject AS a_subj, fa.predicate AS a_pred, fa.value_raw AS a_val,
                  fa.period_raw AS a_per, fa.page AS a_page, fa.grounding AS a_ground,
                  fa.source_quote AS a_quote, da.title AS a_title, da.filename AS a_file,
                  da.publisher AS a_pub,
                  fb.subject AS b_subj, fb.predicate AS b_pred, fb.value_raw AS b_val,
                  fb.period_raw AS b_per, fb.page AS b_page, fb.grounding AS b_ground,
                  fb.source_quote AS b_quote, db_.title AS b_title, db_.filename AS b_file
           FROM relations r
           JOIN facts fa ON fa.id = r.fact_a_id JOIN documents da ON da.id = fa.doc_id
           JOIN facts fb ON fb.id = r.fact_b_id JOIN documents db_ ON db_.id = fb.doc_id
           WHERE lower(fa.subject) = lower(da.publisher) AND lower(fa.subject) != lower(fb.subject)
           ORDER BY r.similarity DESC LIMIT ?""",
        [limit],
    ).fetchall()
    if not rows:
        print("  (none found)")
    for r in rows:
        print(f"\n  [{r['relation_type']}] decided_by={r['decided_by']} sim={r['similarity']:.3f}")
        print(f"    A: subject stored as \"{r['a_subj']}\" -- but that's the publisher of its own"
              f" document, not what the number describes")
        print(f"       {r['a_pred']} = {r['a_val']}  [{r['a_per'] or 'no period'}]")
        print(f"       source: {r['a_title'] or r['a_file']} p.{r['a_page']}  ({r['a_ground']})")
        print(f"       quote : \"{(r['a_quote'] or '')[:150]}\"")
        print(f"    B: subject stored as \"{r['b_subj']}\" (correct -- this is what the number is about)")
        print(f"       {r['b_pred']} = {r['b_val']}  [{r['b_per'] or 'no period'}]")
        print(f"       source: {r['b_title'] or r['b_file']} p.{r['b_page']}  ({r['b_ground']})")
        print(f"       quote : \"{(r['b_quote'] or '')[:150]}\"")
        print(f"    reasoning: {r['explanation']}")
    return 0


if __name__ == "__main__":
    n = 2
    if "--limit" in sys.argv:
        n = int(sys.argv[sys.argv.index("--limit") + 1])
    raise SystemExit(main(n))
