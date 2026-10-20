/** Flags an outcome the system itself rates as a stand-out: not just a high
 * probability, but one backed by enough match history and models that agree
 * with each other (see isHotPick in lib/filters.ts) -- the same bar as the
 * backend's "High-Confidence Prediction Mode". `compact` drops the label for
 * tight table cells, keeping just the mark.
 *
 * Deliberately calm: it used to be a flickering flame on a glowing orange
 * pill labelled "Hot pick" -- an excitement cue on exactly the picks most
 * likely to be bet on. "High confidence" says what the badge actually
 * measures. */
export function HotPickBadge({ compact = false }: { compact?: boolean }) {
  return (
    <span
      className={`hot-pick-badge${compact ? " compact" : ""}`}
      title="High confidence -- high probability, backed by strong data quality and model agreement. Still not a guarantee."
    >
      <span className="hot-pick-flame" aria-hidden>
        ◆
      </span>
      {!compact && <span>High confidence</span>}
    </span>
  );
}
