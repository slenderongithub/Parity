# Parity

A fact knowledge layer for PDFs. It pulls checkable facts out of documents, ties each one
to a verified quote in its source, and figures out when facts across documents agree,
conflict, or just look like they conflict because of time, scope, or units.

Built and tested against Delhivery's corporate filings and three reports on the Indian
economy — but nothing in the pipeline is specific to them. No hard-coded facts, filenames,
schemas, or entity names.

---

## Setup and Run

You'll need a free Gemini key: https://aistudio.google.com/apikey

```bash
cp .env.example .env        # paste your key into GEMINI_API_KEY
```

### Docker

```bash
docker compose up --build
```

UI at http://localhost:5173, API at http://localhost:8000.

### Local

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
(cd backend && ../.venv/bin/python -m uvicorn main:app --port 8000)

# second terminal
cd frontend && npm install && npm run dev
```

The first ingest downloads the embedding model (~130MB, once).

### No API key? No problem

`data/facts.db` ships pre-built from a full run over all six starter PDFs. Start the
backend and browse every fact, quote, and relation with nothing configured — a key is
only needed to ingest *new* PDFs.

### Tests

```bash
.venv/bin/python -m pytest -q
```

72 tests, no API key required. They cover unit/scale/currency parsing, fiscal-year and
quarter arithmetic, the grounding verifier, and the deterministic comparison logic —
the parts that have to be right.

### Batch ingest

```bash
python backend/ingest_all.py "starter-datasets/*/*.pdf"
```

Incremental — re-running only touches PDFs not already in the database. If extraction
fails with `404 NOT_FOUND`, the model name has drifted; run `python backend/list_models.py`
and update `GEMINI_MODEL`.

---

## Video Demo

*[link here]*

A PDF being processed end to end, plus the four required cases below.

---

## Approach

### Why not just "LLM reads PDF, LLM compares facts"

That's the obvious build, and it breaks in two specific places.

An LLM asked to quote its source doesn't always quote it — it paraphrases, merges
lines, or writes something fluent that isn't actually on the page. And an LLM
comparing two numbers (`₹8,142 Cr` vs `₹81,415 Mn`, `FY24` vs `Q4 FY24`) is doing
arithmetic in its head on exactly the comparisons the whole system depends on getting
right.

So the two responsibilities are split. The model does extraction and short,
evidence-in-hand judgement calls. Plain Python does the arithmetic, unit conversion,
and period logic. Every relation records which one made the call (`decided_by: rule`
or `llm`), so nothing is a black box.

### Pipeline

```
PDF → pdfplumber (tables rendered as rows)
    → document-context pass (title, publisher, date, entity, FY convention)
    → chunk ~10 pages
    → Gemini extraction → grounding verifier
    → normalize units + periods
    → embed the claim key locally
    → cosine search against everything stored
    → deterministic comparison → LLM adjudication when it's ambiguous
    → SQLite
```

### Grounding is checked, not assumed

Every quote gets matched against the actual text of the page it claims to come from,
landing in one of three buckets: `verbatim` (found as a contiguous span), `reflowed`
(not contiguous, but every number and most of the words are on the page), or
`unverified` (not supported — kept for visibility, excluded from comparisons).

The middle bucket exists because of something I ran into directly. Delhivery's annual
report has a magazine-style layout, and pdfplumber pulls the columns out interleaved —
so a real fact like `₹81,415 Mn Revenue from services` has no contiguous span in the
extracted text, because the label and figure sit in different columns. Strict
verbatim-only checking threw out 41 of 45 facts from that one document. Checking at
the token level gets them back while still catching fabrication, since a number that
isn't on the page fails no matter how convincing the sentence around it sounds.

### Comparing on the claim, not the value

Facts are embedded by subject + predicate + qualifiers — deliberately excluding the
value. Embed the whole sentence and `GDP growth was 6.5%` drifts away from
`GDP growth was 7.2%` precisely because the numbers differ, which is the one pair a
contradiction detector actually needs to find. Strip the value out and they sit next
to each other, then get compared explicitly.

Search is one matrix multiply against local embeddings (`fastembed`, no torch, no API
cost), so adding a document costs nothing to relate it to everything already stored.

### One rule survived

For each candidate pair, deterministic code computes period relation, unit
comparability, and value agreement — and gets to decide exactly one thing on its own:
same period, comparable units, values within 1% → corroborates, no model call.

The mirror rule — different values over disjoint periods → reconciled by time — didn't
survive. It's trivially true of any two unrelated numbers with different dates. It
produced 97 relations on the first full run, and they were mostly noise: pairs like
`Total equity (FY21)` and `Total assets (FY23)`, joined only because financial line
items read similarly to an embedding. That similarity isn't evidence of sameness, so
those pairs now go to the model, which is allowed to say "unrelated."

Two more guards, both added after a bad output: rules only fire above 0.88 claim-key
similarity, and only on currencies and percentages — bare counts like `"18.8 msf"` vs
`"3,730 sq ft"` go to the model, which can actually read the units.

Currencies are never auto-converted — the right exchange rate is period-specific, and
guessing one would manufacture false precision. INR and USD figures are shown side by
side, flagged as not directly comparable.

### Works beyond the six starter PDFs

Nothing in the pipeline code names a document, entity, or fact — `grep -i delhivery
backend/*.py` comes back empty. Fiscal-year convention (April–March vs
January–December) is detected per document, not assumed, and Indian numbering
(lakh/crore) sits in the same scale table as million/billion. The only domain
knowledge in the system lives in that vocabulary, and it's additive — teaching it a
new convention doesn't touch how the existing ones behave.

### Schema

Facts are a generic envelope — subject, predicate, open-ended qualifiers, value, unit,
period, quote — rather than typed columns per fact kind. The model names the fact type
and invents qualifiers as the documents call for it, so a new document domain adds new
fact shapes with no migration.

### A few trade-offs, made on purpose

- **SQLite over Postgres.** At this scale, candidate search is one matrix multiply.
  A database service wouldn't change any answer, just the ops burden.
- **No task queue.** Ingest progress streams straight from the upload response as
  NDJSON — no broker needed for a single-writer flow.
- **Rate limiting, not parallelism.** The Gemini free tier is the actual constraint,
  so calls are throttled and retried with backoff, and capped per upload
  (`FKL_MAX_ADJUDICATIONS`). Pairs the system never got to are counted, not stored —
  an unexamined pair isn't a finding.
- **Extraction and comparison are separate steps.** `backend/rematch.py` rebuilds
  every relation from what's already stored, without re-reading a single PDF. Every
  rule change described above was tested across the whole corpus this way.

### AI tools used

Claude Code (Sonnet 5 / Opus 5) for implementation. Gemini (`gemini-flash-lite-latest`)
for extraction and adjudication at runtime. `fastembed` with `BAAI/bge-small-en-v1.5`
for local embeddings.

---

## The four required cases

All pulled live from the shipped database with `python backend/report_cases.py` — the
same queries run against any corpus, nothing here is specific to these six PDFs.

### 1. Corroborated across documents

**A:** Delhivery Limited · revenue from services = **₹8,142 Cr** `[FY24]`
source: *Investor Presentation Q4 & FY24* p.6 — *"FY24 revenue from services"*

**B:** Delhivery Limited · Revenue from services = **81,415.38** `[March 31, 2024]`
source: *Annual Report 2023-24* p.85 — *"Revenue from services\* 81,415.38 72,236.47"*

Similarity 1.000. *"₹8,142 Cr is a rounded representation of 81,415.38 million INR for
the same fiscal year."* Two documents, two units, same fact — caught with no hand-coded
knowledge of what "revenue from services" means.

### 2. A genuine contradiction

**A:** India · growth in industrial sector = **4.3%** `[2024-25]`
source: *RBI Annual Report 2024-25* p.8 — *"Growth in industrial sector moderated to
4.3 per cent in 2024-25, primarily due to deceleration in manufacturing GVA."*

**B:** India · industrial sector is estimated to grow by = **6.2 per cent** `[FY25]`
source: *Economic Survey 2024-25* p.14 — *"The industrial sector is estimated to grow
by 6.2 per cent in FY25."*

Same sector, same year, two institutions, two different numbers. Not a units or
timing problem — a real conflict.

### 3. Apparent contradiction, reconciled by context

**A:** Delhivery Limited · Reported EBITDA = **46** `[Q4 FY24]`
source: *Investor Presentation* p.23 — *"Reported EBITDA 13 109 46 (452) 127"*

**B:** Delhivery Limited · adjusted EBITDA = **₹758 Mn** `[FY24]`
source: *Annual Report 2023-24* p.4 — *"Adjusted EBITDA ₹758 Mn"*

Two numbers that would look contradictory side by side turn out to differ on scope
(quarter vs. full year) and measure (reported vs. adjusted) — exactly what the
qualifier fields exist to capture.

### 4. A reasoning failure, found and fixed

The document-context pass would sometimes conflate a report's *publisher* with its
*subject*. For the RBI Annual Report it originally set
`primary_entity = "Reserve Bank of India"` instead of `"India"` — so macro facts from
that document got stored under the wrong subject:

**A:** subject stored as `Reserve Bank of India` (its own publisher — wrong)
external debt to GDP ratio = **19.1 per cent** `[end-December 2024]`
source: *RBI Annual Report* p.89 — *"India's external debt to GDP ratio remained
modest at 19.1 per cent as at end-December 2024"*

**B:** subject stored as `India` (correct)
external debt to GDP ratio = **18.8 per cent** `[end of June 2024]`
source: *Economic Survey 2024-25* p.73

The system still linked A and B — but only because the embedding similarity was loose
enough to survive the wrong subject string. A stricter identity check would have
missed the pair entirely.

I found this with a generic query (any fact whose subject equals its own document's
publisher, still linked to a fact with a different subject) and updated the extraction
prompt in `backend/extract.py` to tell the model that `primary_entity` means "what the
numbers are about," not "who published this." To check the fix actually worked rather
than just hoping it did, I deleted the RBI document from the database and re-ingested
it under the updated prompt. Result: `primary_entity` now resolves to `India`, 112 of
its 116 facts are correctly tagged subject `India`, and the remaining 3 are genuinely
about the RBI itself (its own balance sheet, not the economy) — so that's correct too,
not leftover breakage. Re-running the same generic query against the live corpus now
returns **zero** matches for this failure mode.

That confirms the specific bug is gone. It doesn't mean entity resolution is solved —
there's still no global entity registry, and a document only gets one `primary_entity`
rather than a subject per fact, so a document that genuinely discusses two entities
would still blur them. See Limitations below.

---

## Limitations and Next Steps

Roughly in the order I'd tackle them.

1. **Column interleaving can attach a number to the wrong label, and grounding can't
   catch it.** pdfplumber returns multi-column pages in jumbled order — the RBI report
   is the worst offender. A `reflowed` quote proves the number is on the page, not that
   it belongs to the label the fact assigns it. That's why verbatim and reflowed are
   shown as separate states instead of merged. Next: reconstruct reading order before
   the text reaches the model, by clustering `extract_words` output by column.

2. **Grounding proves the evidence exists, not that the reasoning from it was right.**
   A verbatim quote can still be attached to the wrong subject or period.

3. **Entity resolution is shallow.** The specific publisher-vs-subject bug in Case 4
   is fixed and verified — but there's still no global entity registry, and a document
   only carries one `primary_entity` for all its facts. A document that genuinely
   discusses two entities would still blur them together.
   Next: per-fact subjects instead of one per document, plus alias clustering.

4. **Unitless quantities aren't normalized.** `"18.8 msf"` and `"3,730"` both parse as
   bare counts, so those comparisons always fall to the model. Only currency and
   percentage values get real unit handling today.

5. **Relations are pairwise, not clustered.** Three documents stating the same figure
   produce three edges instead of one claim with three witnesses. This is the next
   change after layout — cluster connected components into a single claim node.

6. **Cross-currency pairs are set aside, not resolved.** Deliberate for now — see
   Approach — but a dated FX table would let INR and USD claims be compared directly.

7. **A per-upload budget can leave pairs undecided.** They're stored as `needs_review`,
   visible in the UI, rather than silently dropped.

8. **Candidate search is brute force.** Fine into the tens of thousands of facts;
   would need an ANN index beyond that.

9. **Extraction isn't perfectly reproducible run to run.** Temperature is 0, but the
   model still varies slightly, so exact counts here won't match a fresh ingest.

10. **No OCR.** Scanned or image-only PDFs yield no text — reported, not silently
    dropped.

11. **Model choice was quota-driven.** Building the shipped corpus burned through the
    daily free quota on two models. The RBI report lost most of its chunks to
    `429 RESOURCE_EXHAUSTED` partway through and had to be re-ingested on a different
    model. This is why quota handling is a first-class part of the pipeline rather
    than an afterthought — rate limiting, backoff, a per-document adjudication budget,
    chunk-level failure isolation, incremental re-ingest, and `rematch.py` so
    comparison logic can be re-run without re-spending extraction calls. All of it
    exists because the build hit the limit it now protects against.

---

## Additional Notes

- `data/facts.db` is committed on purpose, so the system can be evaluated with no API
  key and no paid service. Regenerate it with `python backend/ingest_all.py`.
- Model names drift — `gemini-2.5-flash` stopped being available to new keys partway
  through building this. `backend/list_models.py` plus a configurable `GEMINI_MODEL`
  exist because of that.
- `python backend/report_cases.py` prints the four cases above straight from the
  database — nothing in it is hard-coded to these documents.
- `decided_by` is worth looking at while reviewing — it's the line between what the
  arithmetic decided and what the model decided.
