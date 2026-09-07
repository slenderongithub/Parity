import db


def _seed(conn):
    doc = db.insert_document(conn, filename="a.pdf", title="A", page_count=1)
    f1 = db.insert_fact(conn, doc_id=doc, page=1, subject="India", predicate="gdp growth",
                        value_raw="6.4 per cent", quote_verified=1)
    f2 = db.insert_fact(conn, doc_id=doc, page=2, subject="India", predicate="gdp growth",
                        value_raw="6.5 per cent", quote_verified=1)
    return f1, f2


def test_relation_orientation_is_preserved(tmp_path):
    """Explanations describe the pair in the order the model saw it.

    Sorting the ids on insert would silently swap the two sides, leaving every
    explanation describing the facts the wrong way round.
    """
    conn = db.connect(str(tmp_path / "t.db"))
    f1, f2 = _seed(conn)
    db.insert_relation(conn, fact_a_id=f2, fact_b_id=f1, relation_type="contradicts",
                       decided_by="llm", explanation="the 6.5% figure vs the 6.4% figure")
    row = conn.execute("SELECT fact_a_id, fact_b_id FROM relations").fetchone()
    assert (row["fact_a_id"], row["fact_b_id"]) == (f2, f1)


def test_duplicate_pair_is_rejected_in_either_direction(tmp_path):
    """Re-ingesting must not create a second edge for a pair already related."""
    conn = db.connect(str(tmp_path / "t.db"))
    f1, f2 = _seed(conn)
    assert db.insert_relation(conn, fact_a_id=f1, fact_b_id=f2,
                              relation_type="corroborates", decided_by="rule") is not None
    assert db.insert_relation(conn, fact_a_id=f2, fact_b_id=f1,
                              relation_type="corroborates", decided_by="rule") is None
    assert conn.execute("SELECT count(*) FROM relations").fetchone()[0] == 1


def test_counts_report_grounding_tiers(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    doc = db.insert_document(conn, filename="a.pdf", page_count=1)
    db.insert_fact(conn, doc_id=doc, page=1, quote_verified=1, grounding="verbatim")
    db.insert_fact(conn, doc_id=doc, page=1, quote_verified=1, grounding="reflowed")
    db.insert_fact(conn, doc_id=doc, page=1, quote_verified=0, grounding="unverified")
    c = db.counts(conn)
    assert (c["verbatim"], c["reflowed"], c["ungrounded"], c["grounded"]) == (1, 1, 1, 2)
