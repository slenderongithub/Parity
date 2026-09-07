import { cn } from "@/lib/utils";

export const REL_STYLES = {
  corroborates: {
    label: "Corroborates",
    chip: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    dot: "bg-emerald-400",
    line: "bg-emerald-500/40",
  },
  contradicts: {
    label: "Contradicts",
    chip: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    dot: "bg-rose-400",
    line: "bg-rose-500/40",
  },
  reconciled_time: {
    label: "Reconciled · time",
    chip: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400",
    line: "bg-amber-500/40",
  },
  reconciled_units: {
    label: "Reconciled · units",
    chip: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400",
    line: "bg-amber-500/40",
  },
  reconciled_scope: {
    label: "Reconciled · scope",
    chip: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400",
    line: "bg-amber-500/40",
  },
  needs_review: {
    label: "Needs review",
    chip: "bg-zinc-500/15 text-zinc-300 border-zinc-500/30",
    dot: "bg-zinc-400",
    line: "bg-zinc-500/40",
  },
};

export const relStyle = (t) => REL_STYLES[t] || REL_STYLES.needs_review;

export function Chip({ children, className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        "border-zinc-700 bg-zinc-800/60 text-zinc-300",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Grounding is the core claim of this system, so it is always shown — and shown in
 * three states, because "found verbatim" and "found but reflowed by the PDF's column
 * layout" are different levels of evidence and collapsing them would be misleading.
 */
const GROUNDING = {
  verbatim: {
    text: "✓ verbatim in source",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    title: "The quote was found as a contiguous span on the cited page.",
  },
  reflowed: {
    text: "≈ found, reflowed",
    className: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    title:
      "Every number and almost every word of the quote is on the cited page, but not " +
      "contiguously — typical of multi-column or infographic pages, where PDF text " +
      "extraction interleaves the columns.",
  },
  unverified: {
    text: "✕ not found in source",
    className: "border-rose-500/30 bg-rose-500/10 text-rose-300",
    title: "The quote is not supported by the page. Excluded from relation building.",
  },
};

export function GroundedBadge({ grounding, verified, score }) {
  const g = GROUNDING[grounding] || (verified ? GROUNDING.verbatim : GROUNDING.unverified);
  return (
    <Chip className={g.className} title={g.title}>
      {g.text}
      {score != null ? ` · ${(score * 100).toFixed(0)}%` : ""}
    </Chip>
  );
}

export function DecidedBy({ by }) {
  return (
    <Chip
      className={
        by === "rule"
          ? "border-sky-500/30 bg-sky-500/10 text-sky-300"
          : "border-violet-500/30 bg-violet-500/10 text-violet-300"
      }
      title={
        by === "rule"
          ? "Decided by deterministic period/unit/value rules — no LLM involved"
          : "Decided by the model, with both quotes and the deterministic signals in hand"
      }
    >
      {by === "rule" ? "⚙ rule" : "✦ llm"}
    </Chip>
  );
}

export function Quote({ children }) {
  return (
    <blockquote className="border-l-2 border-zinc-600 pl-3 text-[13px] leading-relaxed text-zinc-300 italic">
      “{children}”
    </blockquote>
  );
}

export function fmtQualifiers(q) {
  if (!q || !q.length) return null;
  return q.map((x) => `${x.key}=${x.value}`).join(" · ");
}
