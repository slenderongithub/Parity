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
    return 0


if __name__ == "__main__":
    n = 2
    if "--limit" in sys.argv:
        n = int(sys.argv[sys.argv.index("--limit") + 1])
    raise SystemExit(main(n))
