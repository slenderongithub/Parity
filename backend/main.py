"""FastAPI app: upload with streaming progress, plus fact/relation browsing."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

import config  # noqa: F401  - loads .env before other modules read the environment
import db
import llm
import pipeline

app = FastAPI(title="Fact Knowledge Layer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # local prototype; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

conn = db.connect()


@app.get("/health")
def health():
    return {"ok": True, "llm": llm.available(), "model": llm.MODEL, **db.counts(conn)}


@app.post("/documents")
async def upload(file: UploadFile = File(...)):
    """Ingest a PDF, streaming NDJSON progress events as extraction runs.

    Streamed over POST (not SSE/EventSource) because EventSource is GET-only and
    cannot carry the file body; the client reads this with fetch + a stream reader.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted")

    suffix = os.path.splitext(file.filename)[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
    finally:
        file.file.close()

    def events():
        try:
            for ev in pipeline.process_pdf(conn, tmp.name, file.filename):
                yield json.dumps(ev, default=str) + "\n"
        except Exception as e:  # noqa: BLE001 - surface failures in-stream
            yield json.dumps({"event": "error", "message": str(e)}) + "\n"
        finally:
            os.unlink(tmp.name)

    return StreamingResponse(
        iterate_in_threadpool(events()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/documents")
def documents():
    rows = conn.execute(
        """SELECT d.*,
                  (SELECT count(*) FROM facts f WHERE f.doc_id=d.id) AS fact_count,
                  (SELECT count(*) FROM facts f WHERE f.doc_id=d.id AND f.quote_verified=1)
                    AS grounded_count
           FROM documents d ORDER BY d.id DESC"""
    ).fetchall()
    return [db.row_to_dict(r) for r in rows]


@app.get("/facts")
def facts(
    doc_id: int | None = None,
    fact_type: str | None = None,
    verified: bool | None = None,
    grounding: str | None = None,
    q: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    sql = ["SELECT f.*, d.filename, d.title FROM facts f JOIN documents d ON d.id=f.doc_id"]
    where, args = [], []
    if doc_id is not None:
        where.append("f.doc_id=?"); args.append(doc_id)
    if fact_type:
        where.append("f.fact_type=?"); args.append(fact_type)
    if verified is not None:
        where.append("f.quote_verified=?"); args.append(int(verified))
    if grounding:
        where.append("f.grounding=?"); args.append(grounding)
    if q:
        where.append("(f.subject LIKE ? OR f.predicate LIKE ? OR f.source_quote LIKE ?)")
        args += [f"%{q}%"] * 3
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY f.id DESC LIMIT ? OFFSET ?")
    args += [limit, offset]
    rows = conn.execute(" ".join(sql), args).fetchall()
    return [db.row_to_dict(r) for r in rows]


@app.get("/facts/{fact_id}")
def fact_detail(fact_id: int):
    row = db.fact_row(conn, fact_id)
    if not row:
        raise HTTPException(404, "No such fact")
    return db.row_to_dict(row)


@app.get("/facts/{fact_id}/relations")
def fact_relations(fact_id: int):
    rows = conn.execute(
        """SELECT r.*,
                  CASE WHEN r.fact_a_id=? THEN r.fact_b_id ELSE r.fact_a_id END AS other_id
           FROM relations r WHERE r.fact_a_id=? OR r.fact_b_id=?
           ORDER BY r.relation_type""",
        [fact_id, fact_id, fact_id],
    ).fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        d["other"] = db.row_to_dict(db.fact_row(conn, r["other_id"]))
        out.append(d)
    return out


@app.get("/relations")
def relations(type: str | None = None, limit: int = Query(100, le=500), offset: int = 0):
    sql = "SELECT * FROM relations"
    args: list = []
    if type:
        sql += " WHERE relation_type=?"
        args.append(type)
    sql += " ORDER BY confidence DESC, id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = db.row_to_dict(r)
        d["a"] = db.row_to_dict(db.fact_row(conn, r["fact_a_id"]))
        d["b"] = db.row_to_dict(db.fact_row(conn, r["fact_b_id"]))
        out.append(d)
    return out


@app.get("/stats")
def stats():
    types = conn.execute(
        "SELECT relation_type, decided_by, count(*) c FROM relations "
        "GROUP BY relation_type, decided_by"
    ).fetchall()
    kinds = conn.execute(
        "SELECT fact_type, count(*) c FROM facts GROUP BY fact_type ORDER BY c DESC LIMIT 20"
    ).fetchall()
    return {
        **db.counts(conn),
        "by_relation": [dict(r) for r in types],
        "by_fact_type": [dict(r) for r in kinds],
    }
