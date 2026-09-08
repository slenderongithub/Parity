import { MagicCard } from "@/components/ui/magic-card";
import { cn } from "@/lib/utils";
import { Chip, DecidedBy, Quote, fmtQualifiers, relStyle } from "./bits";

function Side({ fact }) {
  const quals = fmtQualifiers(fact.qualifiers);
  return (
    <div className="flex-1 min-w-0 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
      <div className="truncate text-[11px] text-zinc-500">
        {fact.title || fact.filename} · p.{fact.page}
      </div>
      <div className="mt-1 text-[13px] font-medium text-zinc-100">
        {fact.subject} · {fact.predicate}
      </div>
      {quals && <div className="mt-0.5 text-[11px] text-zinc-500">{quals}</div>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {fact.value_raw && (
          <span className="min-w-0 max-w-full break-words font-mono text-[13px] text-violet-300">
            {fact.value_raw}
          </span>
        )}
        {fact.period_raw && <Chip>{fact.period_raw}</Chip>}
      </div>
      <div className="mt-2">
        <Quote>{fact.source_quote}</Quote>
      </div>
    </div>
  );
}

export function RelationCard({ rel }) {
  const s = relStyle(rel.relation_type);
  return (
    <MagicCard className="rounded-xl" gradientOpacity={0.35}>
      <div className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold", s.chip)}>
            <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
            {s.label}
          </span>
          <DecidedBy by={rel.decided_by} />
          {rel.similarity != null && <Chip>sim {Number(rel.similarity).toFixed(2)}</Chip>}
          {rel.confidence != null && <Chip>conf {Number(rel.confidence).toFixed(2)}</Chip>}
        </div>

        <div className="mt-3 flex flex-col gap-3 md:flex-row md:items-stretch">
          <Side fact={rel.a} />
          <div className="flex items-center justify-center md:flex-col">
            <div className={cn("h-px w-8 md:h-8 md:w-px", s.line)} />
          </div>
          <Side fact={rel.b} />
        </div>

        {rel.explanation && (
          <p className="mt-3 rounded-lg border border-zinc-800 bg-zinc-900/70 p-3 text-[13px] leading-relaxed text-zinc-300">
            <span className="text-zinc-500">Reasoning · </span>
            {rel.explanation}
          </p>
        )}
      </div>
    </MagicCard>
  );
}
