"""SQLite storage. stdlib sqlite3, no ORM."""

from __future__ import annotations

import json
import os
import sqlite3

DB_PATH = os.getenv("FKL_DB", os.path.join(os.path.dirname(__file__), "..", "data", "facts.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    title TEXT, publisher TEXT, doc_date TEXT,
    primary_entity TEXT, fy_convention TEXT, currency_hint TEXT,
    page_count INTEGER,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER,
    fact_type TEXT, subject TEXT, predicate TEXT,
    qualifiers TEXT,            -- JSON list of {key,value}: the dynamic-schema mechanism
    claim_key TEXT,
    value_raw TEXT, value_num REAL, unit TEXT, dimension TEXT,
    period_raw TEXT, period_start TEXT, period_end TEXT, period_kind TEXT,
    source_quote TEXT, quote_verified INTEGER, quote_score REAL, grounding TEXT,
    confidence REAL,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_a_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    fact_b_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    decided_by TEXT NOT NULL,     -- 'rule' | 'llm'  (keeps reasoning auditable)
    explanation TEXT,
    similarity REAL,
    confidence REAL,
    UNIQUE(fact_a_id, fact_b_id)
);

CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(doc_id);
CREATE INDEX IF NOT EXISTS idx_facts_verified ON facts(quote_verified);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relations(fact_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relations(fact_b_id);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    p = os.path.abspath(path or DB_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # The API server reads while a batch ingest writes; WAL lets that happen without
    # readers blocking, and the timeout absorbs the brief write locks.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA)
    return conn


def insert_document(conn, **kw) -> int:
    cols = ["filename", "title", "publisher", "doc_date", "primary_entity",
            "fy_convention", "currency_hint", "page_count"]
    cur = conn.execute(
        f"INSERT INTO documents ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [kw.get(c) for c in cols],
    )
    conn.commit()
    return cur.lastrowid


def insert_fact(conn, **kw) -> int:
    cols = ["doc_id", "page", "fact_type", "subject", "predicate", "qualifiers",
            "claim_key", "value_raw", "value_num", "unit", "dimension",
            "period_raw", "period_start", "period_end", "period_kind",
            "source_quote", "quote_verified", "quote_score", "grounding", "confidence",
            "embedding"]
    cur = conn.execute(
        f"INSERT INTO facts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [kw.get(c) for c in cols],
    )
    conn.commit()
    return cur.lastrowid


def insert_relation(conn, **kw) -> int | None:
    """Store a relation, preserving the orientation the verdict was made in.

    The pair is NOT reordered: explanations refer to "Claim A" and "Claim B" as they
    were presented to the model, so sorting the ids here would silently swap the two
    sides and make every explanation describe the facts the wrong way round.
    Uniqueness is therefore checked in both directions instead.
    """
    a, b = kw["fact_a_id"], kw["fact_b_id"]
    if conn.execute(
        "SELECT 1 FROM relations WHERE (fact_a_id=? AND fact_b_id=?) OR (fact_a_id=? AND fact_b_id=?)",
        [a, b, b, a],
    ).fetchone():
        return None  # already related; incremental re-ingest must stay idempotent
    try:
        cur = conn.execute(
            """INSERT INTO relations
               (fact_a_id, fact_b_id, relation_type, decided_by, explanation, similarity, confidence)
               VALUES (?,?,?,?,?,?,?)""",
            [a, b, kw["relation_type"], kw["decided_by"], kw.get("explanation"),
             kw.get("similarity"), kw.get("confidence")],
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def verified_facts(conn, exclude_doc_id: int | None = None) -> list[sqlite3.Row]:
    """Grounded facts only — unverified evidence never enters relation building."""
    sql = ("SELECT f.*, d.filename, d.title, d.publisher, d.doc_date "
           "FROM facts f JOIN documents d ON d.id=f.doc_id "
           "WHERE f.quote_verified=1 AND f.embedding IS NOT NULL")
    args: list = []
    if exclude_doc_id is not None:
        sql += " AND f.doc_id != ?"
        args.append(exclude_doc_id)
    return conn.execute(sql, args).fetchall()


def fact_row(conn, fact_id: int):
    return conn.execute(
        """SELECT f.*, d.filename, d.title, d.publisher, d.doc_date
           FROM facts f JOIN documents d ON d.id=f.doc_id WHERE f.id=?""",
        [fact_id],
    ).fetchone()


def row_to_dict(r) -> dict:
    if r is None:
        return {}
    d = {k: r[k] for k in r.keys() if k != "embedding"}
    if d.get("qualifiers"):
        try:
            d["qualifiers"] = json.loads(d["qualifiers"])
        except (TypeError, ValueError):
            d["qualifiers"] = []
    return d


def counts(conn) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return {
        "documents": q("SELECT count(*) FROM documents"),
        "facts": q("SELECT count(*) FROM facts"),
        "grounded": q("SELECT count(*) FROM facts WHERE quote_verified=1"),
        "ungrounded": q("SELECT count(*) FROM facts WHERE quote_verified=0"),
        "verbatim": q("SELECT count(*) FROM facts WHERE grounding='verbatim'"),
        "reflowed": q("SELECT count(*) FROM facts WHERE grounding='reflowed'"),
        "relations": q("SELECT count(*) FROM relations"),
        "contradictions": q("SELECT count(*) FROM relations WHERE relation_type='contradicts'"),
    }
