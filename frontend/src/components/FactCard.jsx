import { cn } from "@/lib/utils";
import { Chip, GroundedBadge, fmtQualifiers } from "./bits";

export function FactCard({ fact, onClick, active }) {
  const quals = fmtQualifiers(fact.qualifiers);
  return (
    <button
      onClick={onClick}
      className={cn(
        "min-w-0 w-full rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-left transition-colors",
        "hover:border-zinc-600 hover:bg-zinc-900",
        active && "border-violet-500/60 bg-zinc-900",
        !fact.quote_verified && "border-dashed border-rose-500/30",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-zinc-100">
            {fact.subject} · {fact.predicate}
          </div>
          {quals && <div className="mt-0.5 truncate text-[11px] text-zinc-500">{quals}</div>}
        </div>
        {fact.value_raw ? (
          <div className="min-w-0 max-w-full shrink break-words text-right font-mono text-[13px] text-violet-300">
            {fact.value_raw}
          </div>
        ) : null}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {fact.period_raw && <Chip>{fact.period_raw}</Chip>}
        {fact.fact_type && <Chip>{fact.fact_type}</Chip>}
        <Chip>p.{fact.page}</Chip>
        <GroundedBadge
          grounding={fact.grounding}
          verified={!!fact.quote_verified}
          score={fact.quote_score}
        />
      </div>
    </button>
  );
}
