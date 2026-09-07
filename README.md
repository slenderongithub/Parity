# Parity

A fact knowledge layer. Extracts checkable facts from PDFs, ties every fact to verified
evidence in its source document, and works out when facts across documents
**corroborate**, **contradict**, or are **reconcilable through context** (time, scope,
units) — i.e. whether they're at parity.

Built against two starter datasets — Delhivery's corporate filings and three
institutional reports on the Indian economy — but nothing in the system is specific to
them: no hard-coded facts, filenames, schemas or document rules.

---

## Setup and Run Instructions

You need a free Gemini API key: <https://aistudio.google.com/apikey>

```bash
cp .env.example .env        # then paste your key into GEMINI_API_KEY
```

### Option A — Local (this is the path I developed and tested on)

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
(cd backend && ../.venv/bin/python -m uvicorn main:app --port 8000)

# in a second terminal
cd frontend && npm install && npm run dev
```

UI at <http://localhost:5173>, API at <http://localhost:8000>.

The first ingest downloads the embedding model (~130MB, once).

### Option B — Docker

```bash
docker compose up --build
```

**Honesty note:** Docker was not installed on my build machine, so while the Compose
setup is complete and reviewed, I could not run it end to end. If it misbehaves, use
Option A, which is fully exercised.

### Without an API key

`data/facts.db` ships pre-built from a full run over all six starter PDFs. Start the
backend and browse every fact, quote and relation in the UI with no key set — only
ingesting *new* PDFs needs one.

### Tests

```bash
.venv/bin/python -m pytest -q      # 72 tests, no API key needed
```

They cover the parts that must not be wrong: unit/scale/currency parsing, Indian
fiscal-year and quarter arithmetic, the grounding verifier (including that it rejects a
fabricated quote), and the deterministic comparison table.

### Batch ingest

```bash
python backend/ingest_all.py "starter-datasets/*/*.pdf"
```

Incremental: re-running only processes PDFs that are not already in the database.

If extraction fails with `404 NOT_FOUND`, model availability has changed —
run `python backend/list_models.py` and set `GEMINI_MODEL` in `.env`.

---

## Video Demo

*(to record)*

---

## Approach

### The problem with the obvious design

The obvious build is: LLM reads PDF → emits facts → LLM compares facts → done. It fails
in two specific ways, and most of this design is a response to them.

**Extracted evidence cannot be trusted as given.** A model asked to quote its source
will sometimes paraphrase, merge two lines, or produce a fluent sentence that is not in
the document. If the quote is wrong, the "grounding" is decoration.

**Values must not be compared by a model.** Asking an LLM whether `₹8,142 Cr` and
`₹81,415 Mn` agree, or whether `FY24` overlaps `Q4 FY24`, invites arithmetic errors on
exactly the comparisons the whole system rests on.

So the work is split:

> **The model does open-ended extraction and short, evidence-in-hand judgement.
> Deterministic Python does arithmetic, unit conversion and period logic.**

Every relation records which of the two decided it (`decided_by`: `rule` or `llm`), so
the reasoning is auditable rather than a black box.

### Pipeline

```
PDF → pdfplumber (per page, tables rendered as rows)
    → document-context pass (title, publisher, date, primary entity, FY convention)
    → chunk ~10 pages, page markers preserved
    → Gemini structured extraction  ──→  GROUNDING VERIFIER
    → normalize units + periods (pure, unit-tested)
    → embed the CLAIM KEY locally (no API cost)
    → cosine candidate search vs everything already stored
    → deterministic comparison table  ──→  LLM adjudication only when ambiguous
    → SQLite
```

### Grounding is verified, not trusted

Every quote is checked against the text of the page it claims to come from, and lands in
one of three states:

| State | Meaning |
|---|---|
| `verbatim` | Contiguous span found on the page. |
| `reflowed` | Not contiguous, but **every number** in the quote and ≥80% of its words are on the page. |
| `unverified` | Not supported by the page. Stored and shown, but excluded from relation building. |

The middle state exists because of a real failure I hit. Delhivery's annual report is a
magazine-style layout; pdfplumber returns its columns interleaved, so a correct fact like
`₹81,415 Mn Revenue from services` has **no contiguous span** in the extracted text — the
label and its figure are separated by an entire column. Strict verbatim checking rejected
41 of 45 facts from that document, including genuinely useful ones. Token-level grounding
recovers them while still rejecting fabrication: an invented figure cannot pass, because
a number that is not on the page fails the check regardless of how fluent the sentence is.

Page attribution is the thing the model gets wrong most often, so a failing quote is
re-checked against the other pages of its chunk and the citation is *corrected* rather
than the fact discarded.

### Comparing on the claim key

Facts are embedded by their **claim key** — subject + predicate + qualifiers, with the
value deliberately excluded.

This matters. Embedding the whole statement pushes `GDP growth was 6.5%` and
`GDP growth was 7.2%` apart precisely because their numbers differ — losing the pair a
contradiction detector most needs to see. Keyed on the claim alone, they sit together
and the values get compared explicitly.

Candidate search is one matrix multiply against stored embeddings, computed locally with
`fastembed` (ONNX, no torch). Adding a document therefore costs **zero** API calls to
find what it relates to — which is what makes incremental ingestion and many-document
corpora affordable.

### The comparison table, and the one rule that survived

For each candidate pair the deterministic layer computes period relation, unit
comparability and value agreement. It is then allowed to decide **exactly one** thing:

| Period | Units | Value | Verdict | LLM? |
|---|---|---|---|---|
| equal | comparable | agree within 1% | `corroborates` | **no** |
| equal | comparable | materially different | → adjudicate | yes |
| disjoint / overlapping / unknown | any | any | → adjudicate | yes |
| any | different currency | any | → adjudicate (flagged as FX-incomparable) | yes |
| unitless counts, or non-numeric | — | — | → adjudicate | yes |

That narrowness is the point, and it was earned the hard way.

The rule that survives is **self-validating**: two values that agree numerically for the
same period are almost certainly the same claim, because agreement on both is a strong
coincidence otherwise. So `₹8,142 Cr` (earnings deck) and `81,415.38` in a `₹ Mn` table
(annual report) can be joined by arithmetic alone, with no model call.

The obvious mirror rule — *different values over disjoint periods → reconciled by time* —
**is not self-validating and was removed.** It is trivially satisfied by any two unrelated
numbers carrying different dates, so it asserts a relationship on no evidence. In the
first full corpus run it produced 97 relations, and sampling them showed pairs like
`Total equity 48,465.92 (FY21)` ↔ `Total assets 112,134.30 (FY23)` and
`Total income` ↔ `Diluted loss per share` — all at 0.88–0.93 claim-key similarity, because
financial line items are lexically near-identical. Similarity that high is *not* evidence
of sameness, so no similarity threshold could have fixed it. Those pairs now go to the
model, which can answer `unrelated`. (A regression test pins this: see
`test_unrelated_metrics_at_different_dates_are_never_auto_reconciled`.)

Two further guards, each added after a real bad output:

- Rules only fire above 0.88 claim-key similarity.
- Rules only run on **currencies and percentages**. `"18.8 msf"` and `"3,730 sq ft"` both
  parse as bare counts, so the arithmetic cannot tell they are in different units; those
  pairs go to the model, which can read the units off the quotes.

When the model is asked, it gets both quotes, both sets of qualifiers, and the
deterministic signals, and must choose from a fixed label set and name the deciding
attribute. It is instructed to prefer a reconciliation over a contradiction where the
evidence supports one — but explicitly not to invent a reconciliation, because an
unexplained conflict *is* a contradiction.

**Currencies are never auto-converted.** The correct rate is period-dependent, and
picking one silently would manufacture false precision — so INR and USD figures are
labelled "not directly comparable without an exchange rate" with both values shown.

### Generalizing to unseen documents

There are no filenames, entities or facts in the pipeline code — `grep -i delhivery
backend/*.py` returns nothing. Two things that look domain-specific are not:

- **The fiscal-year convention is detected per document**, not assumed. The context pass
  classifies each document `IN` (April–March) or `CY` (January–December), and every period
  in that document is parsed under its own convention. A US filing saying "FY2024" gets
  calendar-year bounds; an Indian one gets April–March.
- **Indian numbering is supported, not required.** `lakh`/`crore` sit in the same scale
  table as `million`/`billion`, so a document using either is parsed the same way.

The only per-domain knowledge the system carries is in the *vocabulary* of the normalizer
(scale words, currency symbols, period formats), which is additive: teaching it a new
convention does not change how any existing one behaves.

### Schema

Facts are stored as a generic envelope — subject, predicate, `qualifiers` (open JSON),
value, unit, period, quote — rather than typed columns per fact kind. The model names the
`fact_type` and invents qualifier keys as the documents require, so a new document domain
brings new fact shapes with no migration and no code change. Nothing in the matching
logic depends on any particular type.

### Trade-offs

- **SQLite, not Postgres/pgvector.** At this corpus size the candidate search is one
  matrix multiply over a few thousand vectors. Postgres would add a service to run
  without changing any answer.
- **No task queue.** Progress is streamed from the ingest loop itself as NDJSON over the
  upload response, so the UI shows real progress without a broker. (`EventSource` is
  GET-only and cannot carry a file body, so the client reads the POST response stream
  directly.)
- **Rate limiting over parallelism.** The Gemini free tier is the binding constraint, so
  calls are spaced by a token-bucket limiter, retried with backoff on 429, and capped per
  upload (`FKL_MAX_ADJUDICATIONS`) so a large corpus cannot cause a call explosion. When
  that budget runs out, the remaining pairs are **counted, not stored**: a pair the system
  never examined is not a relation, and writing an edge for it would assert a finding that
  neither a rule nor the model ever made.
- **Extraction and comparison are separable.** `backend/rematch.py` rebuilds every
  relation from stored facts and embeddings without re-reading a PDF. Extraction is the
  expensive half; comparison logic is the half that changes. Every rule change described
  above was evaluated across the whole corpus this way, for the cost of the adjudication
  calls alone.
- **Extraction is capped per chunk** and prompted for *salient, checkable* claims rather
  than everything, because exhaustive extraction over 600 pages is mostly noise.

### AI tools used

Claude Code (Sonnet 5 / Opus 5) for implementation. Gemini (`gemini-flash-lite-latest`)
for extraction and adjudication at runtime. `fastembed` with `BAAI/bge-small-en-v1.5` for
local embeddings.

---

## The four required cases

Pulled straight from the shipped database with `python backend/report_cases.py` — nothing
below is hard-coded to these documents; the same queries run against any corpus.

### 1. Corroborated across documents

> **A:** Delhivery Limited · revenue from services = **₹8,142 Cr** `[FY24]`
> source: *Investor Presentation Q4 & FY24* p.6 (verbatim) — *"FY24 revenue from services"*
>
> **B:** Delhivery Limited · Revenue from services = **81,415.38** `[March 31, 2024]`
> source: *Annual Report 2023-24* p.85 (verbatim) — *"Revenue from services\* 81,415.38 72,236.47"*

`decided_by=llm`, similarity 1.000. Reasoning: *"₹8,142 Cr is a rounded representation of
81,415.38 million INR for the same fiscal year."* Two different documents, two different
units (crore vs. million), same underlying fact — caught without any hard-coded knowledge
of what "revenue from services" means.

### 2. A genuine contradiction

> **A:** India · CPI inflation projection = **4.0 per cent** `[2025-26]`
> source: *RBI Annual Report* p.17 (reflowed) — *"CPI inflation for 2025-26 is projected at
> 4.0 per cent, with risks evenly balanced."*
>
> **B:** India · RBI inflation projection = **4.2 per cent** `[FY26]`
> source: *Economic Survey 2024-25* p.87 (verbatim) — *"the RBI expects headline inflation
> to be 4.2 per cent in FY26."*

`decided_by=llm`, similarity 0.884, confidence 0.95. Same institution's own projection for
the same fiscal year, reported two different ways in two documents — a real conflict, not
an artifact of units or timing.

### 3. Apparent contradiction reconciled by context

> **A:** Delhivery Limited · Reported EBITDA = **46** `[Q4 FY24]`
> source: *Investor Presentation* p.23 (verbatim) — *"Reported EBITDA 13 109 46 (452) 127"*
>
> **B:** Delhivery Limited · adjusted EBITDA = **₹758 Mn** `[FY24]`
> source: *Annual Report 2023-24* p.4 (reflowed) — *"Adjusted EBITDA ₹758 Mn"*

`decided_by=llm`, similarity 0.924, confidence 1.0. Reasoning: *"the figures represent
different metrics — Reported EBITDA of 46 Mn for Q4 FY24 versus Adjusted EBITDA of 758 Mn
for the full FY24."* Two numbers that would look contradictory in isolation are reconciled
once **scope** (quarter vs. full year) and **measure** (reported vs. adjusted) are both
accounted for — exactly the kind of qualifier the extraction schema exists to capture.

### 4. A reasoning failure, found and only partly handled

Entity resolution conflates the document's **publisher** with its **subject**. The
context pass for the RBI Annual Report set `primary_entity = "Reserve Bank of India"`
(the report's author) instead of `"India"` (what most of its numeric claims are actually
about), so macro facts from that document got stored with subject `Reserve Bank of India`:

> **A:** subject stored as `Reserve Bank of India` (its own document's publisher — wrong)
> external debt to GDP ratio = **19.1 per cent** `[as at end-December 2024]`
> source: *RBI Annual Report* p.89 (reflowed) — *"India's external debt to GDP ratio
> remained modest at 19.1 per cent as at end-December 2024"*
>
> **B:** subject stored as `India` (correct)
> external debt to GDP ratio = **18.8 per cent** `[end of June 2024]`
> source: *Economic Survey 2024-25* p.73 (verbatim)

The system still linked A and B (`reconciled_time`, similarity 0.948) — but only because
claim-key embedding similarity was forgiving enough to survive the wrong subject string.
Identity did no real work here; a stricter entity check would have missed this pair
entirely. **Found** by a generic query (`report_cases.py`'s "entity resolution" section:
any fact whose subject equals its own document's publisher, still linked to a fact with a
different subject) — no filename or entity name is hard-coded, so the same query would
catch this failure mode in an unseen document. **Handled partially**: the doc-context and
extraction prompts (`backend/extract.py`) now explicitly instruct the model that
`primary_entity` is "what the numbers are about," not "who published this," and to never
resolve a country/economy claim to the publisher's name — which should reduce this for
future ingests, though it wasn't re-run against the shipped corpus to avoid spending more
of the free API quota re-extracting documents that already work. **Would still improve**:
a real fix needs a per-fact subject rather than a single per-document `primary_entity`, and
an entity table with alias clustering so subjects match by identity, not string/embedding
similarity — see Limitations #3.

---

## Limitations and Next Steps

Honest list, in roughly the order I would fix them.

**1. Column interleaving can attach a number to the wrong label — and grounding cannot
catch it.** This is the most serious limitation. `pdfplumber` returns multi-column and
infographic pages in jumbled reading order (the RBI Annual Report is the worst case:
`"global growth at 3.3 per cent monetary policy trajectories of major central"` is two
columns spliced together). Token-level grounding proves the number appears on the page;
it does **not** prove the number belongs to the label the fact assigns it. So a
`reflowed` fact is weaker evidence than a `verbatim` one, which is exactly why the two
are shown as different states rather than merged into "grounded".
*Next:* reconstruct reading order before the text reaches the model — cluster
`extract_words` output by x-position into columns, or use a layout model. This would
raise extraction quality more than any prompt change.

**2. Grounding proves provenance, not correctness.** A verbatim quote can still be
attached to the wrong subject or period by the extractor. The system verifies that the
evidence exists, not that the reasoning from it was right.

**3. Entity resolution is shallow, and confuses the publisher with the subject.**
`"the Company"` and `"we"` are resolved to the document's primary entity, but there is no
global entity registry — see the full walkthrough of a real instance of this (RBI Annual
Report facts stored under subject `Reserve Bank of India` instead of `India`) in
**Case 4** above, including the prompt change made in response.
*Next:* a per-fact subject instead of one per-document `primary_entity`, plus an entity
table with alias clustering so subjects match by identity rather than string similarity.

**4. Unitless quantities are not normalized.** `"18.8 msf"` and `"3,730"` both parse as
bare counts, so the deterministic path cannot compare them and defers to the model. Only
currencies and percentages are normalized properly.
*Next:* a dimensional-units lexicon (area, mass, distance, headcount) so physical
quantities get the same treatment as money.

**5. Relations are pairwise, not clustered.** Three documents asserting the same figure
produce three separate edges rather than one claim with three witnesses. This is the
change I would make next after layout: cluster connected components into a single claim
node carrying supporting and conflicting evidence, which is both a better data model and
a much better UI.

**6. Cross-currency pairs are declined, not resolved.** Deliberate — see Approach — but a
dated FX table would let INR and USD claims be compared with an explicit, cited rate
instead of being set aside.

**7. Budgets can leave pairs undecided.** With `FKL_MAX_ADJUDICATIONS` reached, remaining
ambiguous pairs are stored as `needs_review` rather than silently dropped — visible in the
UI, but unresolved.

**8. Candidate search is brute force.** One matrix multiply over all stored embeddings per
chunk. Fine into the tens of thousands of facts; beyond that it needs an ANN index or
pgvector.

**9. Extraction is not reproducible run-to-run.** Temperature is 0, but the model still
returns slightly different fact sets across runs, so counts in this README will not match
a fresh ingest exactly.

**10. No OCR.** Scanned or image-only PDFs yield no text; the pipeline reports this
rather than failing silently.

**11. Model choice is quota-driven, and the free tier is genuinely small.** Building the
shipped corpus exhausted the daily free quota on two models in succession. `gemini-2.5-flash`
404s for new keys; `gemini-3.6-flash` ran out first; `gemini-flash-lite-latest` then ran out
part-way through the RBI report, losing 8 of its 10 chunks to `429 RESOURCE_EXHAUSTED`
before I dropped that document and re-ingested it on `gemini-3.1-flash-lite`. The corpus
was therefore built across more than one model, and a stronger model would produce better
*qualifiers* — which is what most reconciliation decisions actually turn on.

This is why the pipeline treats quota as a first-class constraint rather than an
afterthought: per-call rate limiting, backoff on 429, a per-document adjudication budget,
chunk-level failure isolation (one dead chunk does not kill the upload), incremental
ingestion that skips completed documents on re-run, and `rematch.py` so comparison logic
can be re-run without re-spending extraction calls. Every one of those existed because
the build hit the limit it protects against.

---

## Additional Notes

- **`data/facts.db` is committed on purpose** so the system can be evaluated without an
  API key or a paid service. Regenerate it with `python backend/ingest_all.py`.
- **Model names drift.** `gemini-2.5-flash` returned `404 … no longer available to new
  users` partway through building this, and the larger models' free quota ran out. Hence
  `backend/list_models.py` and a configurable `GEMINI_MODEL`.
- **`python backend/report_cases.py`** prints the four required cases with evidence
  straight from the database — nothing in it is hard-coded to the starter documents.
- The `decided_by` field is worth looking at while reviewing: it separates conclusions
  the arithmetic reached from conclusions the model reached.
