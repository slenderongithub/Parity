"""Batch-ingest PDFs into the knowledge layer.

Used to build the shipped data/facts.db so the UI can be explored without an
API key. Ingestion is incremental: run it again with new PDFs and only those are
extracted and compared against what is already stored.

    python backend/ingest_all.py starter-datasets/*/*.pdf
"""

from __future__ import annotations

import glob
import os
import sys
import time

import config  # noqa: F401  - loads .env
import db
import pipeline


def main(paths: list[str]) -> int:
    files = [p for arg in paths for p in sorted(glob.glob(arg)) if p.lower().endswith(".pdf")]
    if not files:
        print("No PDFs matched.")
        return 1

    conn = db.connect()
    already = {r["filename"] for r in conn.execute("SELECT filename FROM documents")}
    started = time.time()

    for path in files:
        name = os.path.basename(path)
        if name in already:
            print(f"skip  {name} (already ingested)")
            continue
        print(f"\n=== {name} ===", flush=True)
        counts = {"verbatim": 0, "reflowed": 0, "unverified": 0}
        rels: dict[str, int] = {}
        for ev in pipeline.process_pdf(conn, path, name):
            kind = ev["event"]
            if kind == "fact":
                counts[ev["grounding"]] += 1
            elif kind == "relation":
                rels[ev["type"]] = rels.get(ev["type"], 0) + 1
                print(f"  {ev['type']:18} [{ev['decided_by']}] {(ev['explanation'] or '')[:110]}",
                      flush=True)
            elif kind == "doc":
                print(f"  {ev['title']} | {ev['publisher']} | {ev['doc_date']} | "
                      f"{ev['pages']}p | FY={ev['fy_convention']}", flush=True)
            elif kind in ("note", "error"):
                print(f"  ! {ev.get('message')}"[:200], flush=True)
            elif kind == "done":
                print(f"  facts: {counts} relations: {rels}", flush=True)

    print(f"\nTotals after {time.time() - started:.0f}s: {db.counts(conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["starter-datasets/*/*.pdf"]))
