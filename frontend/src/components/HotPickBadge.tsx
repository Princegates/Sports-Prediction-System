/** Flags an outcome the system itself rates as a stand-out: not just a high
 * probability, but one backed by enough match history and models that agree
 * with each other (see isHotPick in lib/filters.ts) -- the same bar as the
 * backend's "High-Confidence Prediction Mode". `compact` drops the label for
 * tight table cells, keeping just the animated flame. */
export function HotPickBadge({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`hot-pick-badge${compact ? " compact" : ""}`} title="Hot pick -- high probability, backed by strong data quality and model agreement">
      <span className="hot-pick-flame" aria-hidden>
        🔥
      </span>
      {!compact && <span>Hot pick</span>}
    </span>
  );
}
