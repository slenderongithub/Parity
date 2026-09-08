import { useEffect, useState } from "react";

import { BlurFade } from "@/components/ui/blur-fade";
import { MagicCard } from "@/components/ui/magic-card";
import { getFactRelations } from "@/api";
import { cn } from "@/lib/utils";
import { Chip, DecidedBy, GroundedBadge, Quote, fmtQualifiers, relStyle } from "./bits";

function Row({ k, v }) {
  if (v === null || v === undefined || v === "") return null;
  return (
    <div className="flex gap-3 py-1 text-[12px]">
      <span className="w-28 shrink-0 text-zinc-500">{k}</span>
      <span className="min-w-0 break-words text-zinc-300">{String(v)}</span>
    </div>
  );
}

export function FactDetail({ fact, onSelectFact }) {
  const [rels, setRels] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!fact) return;
    setLoading(true);
    getFactRelations(fact.id)
      .then(setRels)
      .catch(() => setRels([]))
      .finally(() => setLoading(false));
  }, [fact]);

  if (!fact) {
    return (
      <div className="flex min-h-[140px] items-center justify-center p-6 text-center text-[13px] text-zinc-600 lg:h-full">
        Select a fact to see its evidence and cross-document relations.
      </div>
    );
  }

  const quals = fmtQualifiers(fact.qualifiers);

  return (
    <BlurFade
      key={fact.id}
      className="max-h-[65vh] overflow-y-auto p-4 lg:h-full lg:max-h-none"
      duration={0.35}
    >
      <div className="text-[11px] text-zinc-500">
        {fact.title || fact.filename} · page {fact.page}
      </div>
      <h2 className="mt-1 break-words text-[15px] font-semibold text-zinc-100">
        {fact.subject} · {fact.predicate}
      </h2>
      {quals && <div className="mt-1 break-words text-[12px] text-zinc-500">{quals}</div>}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {fact.value_raw && (
          <span className="min-w-0 max-w-full break-words font-mono text-[15px] text-violet-300">
            {fact.value_raw}
          </span>
        )}
        {fact.period_raw && <Chip>{fact.period_raw}</Chip>}
        <GroundedBadge
          grounding={fact.grounding}
          verified={!!fact.quote_verified}
          score={fact.quote_score}
        />
      </div>

      {/* The grounding receipt: the span this fact was read from, and how well it checked out. */}
      <div className="mt-4">
        <div className="mb-1.5 text-[11px] tracking-wide text-zinc-500 uppercase">
          Source evidence
        </div>
        <MagicCard className="rounded-lg" gradientOpacity={0.3}>
          <div className="p-3">
            <Quote>{fact.source_quote}</Quote>
          </div>
        </MagicCard>
      </div>

      <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
        <div className="mb-1 text-[11px] tracking-wide text-zinc-500 uppercase">
          Normalized for comparison
        </div>
        <Row k="value" v={fact.value_num} />
        <Row k="unit" v={fact.unit} />
        <Row k="dimension" v={fact.dimension} />
        <Row k="period" v={fact.period_start ? `${fact.period_start} → ${fact.period_end}` : null} />
        <Row k="period kind" v={fact.period_kind} />
        <Row k="fact type" v={fact.fact_type} />
        <Row k="confidence" v={fact.confidence} />
        <Row k="claim key" v={fact.claim_key} />
      </div>

      <div className="mt-4">
        <div className="mb-1.5 text-[11px] tracking-wide text-zinc-500 uppercase">
          Related across documents {rels.length ? `(${rels.length})` : ""}
        </div>
        {loading && <div className="text-[12px] text-zinc-600">Loading…</div>}
        {!loading && !rels.length && (
          <div className="text-[12px] text-zinc-600">
            No cross-document relation found for this fact.
          </div>
        )}
        <div className="flex flex-col gap-2">
          {rels.map((r) => {
            const s = relStyle(r.relation_type);
            return (
              <div key={r.id} className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                      s.chip,
                    )}
                  >
                    <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
                    {s.label}
                  </span>
                  <DecidedBy by={r.decided_by} />
                </div>
                <button
                  onClick={() => onSelectFact?.(r.other)}
                  className="mt-2 block w-full text-left"
                >
                  <div className="text-[11px] text-zinc-500">
                    {r.other.title || r.other.filename} · p.{r.other.page}
                  </div>
                  <div className="break-words text-[13px] text-zinc-200">
                    {r.other.subject} · {r.other.predicate}{" "}
                    {r.other.value_raw && (
                      <span className="break-words font-mono text-violet-300">
                        {r.other.value_raw}
                      </span>
                    )}{" "}
                    {r.other.period_raw && (
                      <span className="text-zinc-500">({r.other.period_raw})</span>
                    )}
                  </div>
                </button>
                {r.explanation && (
                  <p className="mt-2 text-[12px] leading-relaxed text-zinc-400">{r.explanation}</p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </BlurFade>
  );
}
