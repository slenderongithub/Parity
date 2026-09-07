import { useCallback, useEffect, useRef, useState } from "react";

import { getDocuments, getFacts, getRelations, getStats, uploadPdf } from "@/api";
import { FactCard } from "@/components/FactCard";
import { FactDetail } from "@/components/FactDetail";
import { RelationCard } from "@/components/RelationCard";
import { Chip } from "@/components/bits";
import { StreamList } from "@/components/ui/animated-list";
import { BorderBeam } from "@/components/ui/border-beam";
import { MagicCard } from "@/components/ui/magic-card";
import { NumberTicker } from "@/components/ui/number-ticker";
import { ShimmerButton } from "@/components/ui/shimmer-button";
import { cn } from "@/lib/utils";

const REL_TABS = [
  ["", "All"],
  ["corroborates", "Corroborated"],
  ["contradicts", "Contradictions"],
  ["reconciled_time", "Reconciled · time"],
  ["reconciled_units", "Reconciled · units"],
  ["reconciled_scope", "Reconciled · scope"],
  ["needs_review", "Needs review"],
];

function Stat({ label, value, tone }) {
  return (
    <div className="flex flex-col">
      <span className={cn("text-xl font-semibold", tone || "text-zinc-100")}>
        <NumberTicker value={value || 0} />
      </span>
      <span className="text-[11px] text-zinc-500">{label}</span>
    </div>
  );
}

export default function App() {
  const [stats, setStats] = useState({});
  const [docs, setDocs] = useState([]);
  const [facts, setFacts] = useState([]);
  const [relations, setRelations] = useState([]);
  const [selected, setSelected] = useState(null);

  const [tab, setTab] = useState("facts");
  const [relType, setRelType] = useState("");
  const [docFilter, setDocFilter] = useState(null);
  const [groundingFilter, setGroundingFilter] = useState(null);
  const [query, setQuery] = useState("");

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState([]);
  const [live, setLive] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const [s, d, f] = await Promise.all([
        getStats(),
        getDocuments(),
        getFacts({ docId: docFilter, grounding: groundingFilter, q: query }),
      ]);
      setStats(s);
      setDocs(d);
      setFacts(f);
      setError(null);
    } catch (e) {
      setError(`Cannot reach the API (${e.message}). Is the backend running on port 8000?`);
    }
  }, [docFilter, groundingFilter, query]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (tab === "relations") getRelations(relType).then(setRelations).catch(() => setRelations([]));
  }, [tab, relType, stats.relations]);

  async function handleFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setLive([]);
    setProgress([]);
    setTab("facts");

    for (const file of files) {
      setProgress((p) => [...p, { kind: "info", text: `Processing ${file.name}…` }]);
      try {
        await uploadPdf(file, (ev) => {
          if (ev.event === "fact") {
            setLive((l) => [ev, ...l].slice(0, 60));
          } else if (ev.event === "relation") {
            setProgress((p) => [
              ...p,
              { kind: ev.type, text: `${ev.type} · ${ev.explanation || ""}` },
            ]);
          } else if (ev.event === "doc") {
            setProgress((p) => [
              ...p,
              { kind: "info", text: `${ev.title || ev.filename} · ${ev.pages} pages · ${ev.publisher || "unknown publisher"}` },
            ]);
          } else if (ev.event === "plan") {
            setProgress((p) => [...p, { kind: "info", text: `${ev.chunks} chunks to extract` }]);
          } else if (ev.event === "note") {
            setProgress((p) => [...p, { kind: "warn", text: ev.message }]);
          } else if (ev.event === "error") {
            setError(ev.message);
          } else if (ev.event === "done") {
            setProgress((p) => [
              ...p,
              {
                kind: "ok",
                text:
                  `Done · ${ev.totals.facts} facts (${ev.totals.grounded} grounded, ` +
                  `${ev.totals.ungrounded} rejected) · ${ev.totals.relations} relations` +
                  (ev.totals.unexamined
                    ? ` · ${ev.totals.unexamined} pairs left unexamined (budget)`
                    : ""),
              },
            ]);
          }
        });
      } catch (e) {
        setError(String(e.message || e));
      }
      await refresh();
    }
    setBusy(false);
  }

  return (
    <div className="mx-auto flex h-full max-w-[1500px] flex-col">
      <header className="flex flex-wrap items-end justify-between gap-6 border-b border-zinc-800 px-6 py-4">
        <div>
          <h1 className="text-[17px] font-semibold text-zinc-50">Fact Knowledge Layer</h1>
          <p className="text-[12px] text-zinc-500">
            Grounded facts from PDFs, compared across documents.
          </p>
        </div>
        <div className="flex gap-7">
          <Stat label="documents" value={stats.documents} />
          <Stat label="facts" value={stats.facts} />
          <Stat label="verbatim" value={stats.verbatim} tone="text-emerald-300" />
          <Stat label="reflowed" value={stats.reflowed} tone="text-sky-300" />
          <Stat label="rejected" value={stats.ungrounded} tone="text-rose-300" />
          <Stat label="relations" value={stats.relations} />
          <Stat label="contradictions" value={stats.contradictions} tone="text-rose-300" />
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row lg:overflow-hidden">
        {/* left: upload + documents */}
        <aside className="w-full shrink-0 space-y-4 border-b border-zinc-800 p-4 lg:w-[320px] lg:overflow-y-auto lg:border-r lg:border-b-0">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              handleFiles(e.dataTransfer.files);
            }}
            className={cn(
              "relative flex flex-col items-center gap-3 rounded-xl border border-dashed p-6 text-center transition-colors",
              dragging ? "border-violet-400 bg-violet-500/5" : "border-zinc-700 bg-zinc-900/40",
            )}
          >
            {/* the beam runs only while the pipeline is actually working */}
            {busy && <BorderBeam size={70} duration={4} borderWidth={2} />}
            <div className="text-[13px] text-zinc-300">
              {busy ? "Extracting…" : "Drop PDFs here"}
            </div>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf"
              multiple
              hidden
              onChange={(e) => handleFiles(e.target.files)}
            />
            <ShimmerButton
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              className="text-[13px]"
              background="rgba(24,24,27,1)"
            >
              {busy ? "Working…" : "Choose PDFs"}
            </ShimmerButton>
          </div>

          {error && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-[12px] text-rose-200">
              {error}
            </div>
          )}

          {progress.length > 0 && (
            <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
              {progress.map((p, i) => (
                <div
                  key={i}
                  className={cn(
                    "text-[11px] leading-relaxed",
                    p.kind === "ok" && "text-emerald-300",
                    p.kind === "warn" && "text-amber-300",
                    p.kind === "contradicts" && "text-rose-300",
                    p.kind === "corroborates" && "text-emerald-300",
                    p.kind?.startsWith("reconciled") && "text-amber-300",
                    p.kind === "info" && "text-zinc-400",
                  )}
                >
                  {p.text}
                </div>
              ))}
            </div>
          )}

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] tracking-wide text-zinc-500 uppercase">Documents</span>
              {docFilter && (
                <button
                  onClick={() => setDocFilter(null)}
                  className="text-[11px] text-violet-300 hover:underline"
                >
                  clear filter
                </button>
              )}
            </div>
            <div className="space-y-2">
              {docs.map((d) => (
                <MagicCard key={d.id} className="rounded-lg" gradientOpacity={0.3}>
                  <button
                    onClick={() => setDocFilter(docFilter === d.id ? null : d.id)}
                    className={cn(
                      "w-full p-3 text-left",
                      docFilter === d.id && "bg-violet-500/5",
                    )}
                  >
                    <div className="truncate text-[13px] font-medium text-zinc-100">
                      {d.title || d.filename}
                    </div>
                    <div className="truncate text-[11px] text-zinc-500">
                      {d.publisher || "—"} · {d.doc_date || "no date"}
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      <Chip>{d.page_count}p</Chip>
                      <Chip>{d.grounded_count}/{d.fact_count} grounded</Chip>
                      {d.fy_convention && <Chip>{d.fy_convention} FY</Chip>}
                    </div>
                  </button>
                </MagicCard>
              ))}
              {!docs.length && (
                <div className="text-[12px] text-zinc-600">No documents ingested yet.</div>
              )}
            </div>
          </div>
        </aside>

        {/* middle: facts / relations */}
        <main className="flex min-h-[60vh] min-w-0 flex-1 flex-col lg:min-h-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-4 py-2.5">
            {["facts", "relations"].map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-[12px] font-medium capitalize transition-colors",
                  tab === t
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-300",
                )}
              >
                {t}
              </button>
            ))}

            <div className="ml-auto flex items-center gap-2">
              {tab === "facts" && (
                <>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="search facts…"
                    className="w-44 rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-zinc-600"
                  />
                  {[
                    [null, "all"],
                    ["verbatim", "verbatim"],
                    ["reflowed", "reflowed"],
                    ["unverified", "rejected"],
                  ].map(([v, label]) => (
                    <button
                      key={label}
                      onClick={() => setGroundingFilter(v)}
                      className={cn(
                        "rounded-md px-2 py-1 text-[11px]",
                        groundingFilter === v
                          ? "bg-zinc-800 text-zinc-100"
                          : "text-zinc-500 hover:text-zinc-300",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </>
              )}
            </div>
          </div>

          {tab === "relations" && (
            <div className="flex flex-wrap gap-1.5 border-b border-zinc-800 px-4 py-2">
              {REL_TABS.map(([v, label]) => (
                <button
                  key={label}
                  onClick={() => setRelType(v)}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px]",
                    relType === v
                      ? "border-zinc-600 bg-zinc-800 text-zinc-100"
                      : "border-zinc-800 text-zinc-500 hover:text-zinc-300",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {tab === "facts" ? (
              <>
                {busy && live.length > 0 && (
                  <div className="mb-4">
                    <div className="mb-2 text-[11px] tracking-wide text-zinc-500 uppercase">
                      Live extraction
                    </div>
                    <StreamList>
                      {live.map((f) => (
                        <FactCard key={`live-${f.id}`} fact={f} onClick={() => {}} />
                      ))}
                    </StreamList>
                  </div>
                )}
                {!busy && (
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {facts.map((f) => (
                      <FactCard
                        key={f.id}
                        fact={f}
                        active={selected?.id === f.id}
                        onClick={() => setSelected(f)}
                      />
                    ))}
                  </div>
                )}
                {!busy && !facts.length && (
                  <div className="py-16 text-center text-[13px] text-zinc-600">
                    No facts yet. Upload a PDF to begin.
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col gap-3">
                {relations.map((r) => (
                  <RelationCard key={r.id} rel={r} />
                ))}
                {!relations.length && (
                  <div className="py-16 text-center text-[13px] text-zinc-600">
                    No relations of this type yet.
                  </div>
                )}
              </div>
            )}
          </div>
        </main>

        {/* right: fact detail */}
        <section className="w-full shrink-0 border-t border-zinc-800 lg:w-[380px] lg:border-t-0 lg:border-l">
          <FactDetail fact={selected} onSelectFact={setSelected} />
        </section>
      </div>
    </div>
  );
}
