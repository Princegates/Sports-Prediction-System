interface Props {
  scores: Record<string, number>;
  columns?: number;
}

export function ScoreHeatmap({ scores, columns = 4 }: Props) {
  const entries = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <p className="badge-neutral">No correct-score distribution available for this match.</p>;
  }
  const max = entries[0][1];

  return (
    <div className="score-grid-wrapper">
      <div className="score-grid" style={{ gridTemplateColumns: `repeat(${Math.min(entries.length, columns)}, 1fr)` }}>
        {entries.map(([score, p], i) => (
          <div key={score} className={`score-cell${i === 0 ? " top" : ""}`} style={{ opacity: 0.45 + 0.55 * (p / max) }}>
            <span className="score">{score}</span>
            <span className="pct tabular-nums">{(p * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
