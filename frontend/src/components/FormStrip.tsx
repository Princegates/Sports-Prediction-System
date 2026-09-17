export function FormStrip({ results }: { results: string[] }) {
  if (results.length === 0) {
    return <span className="badge-neutral">No recent form</span>;
  }
  return (
    <div className="form-strip">
      {results.slice(-6).map((r, i) => (
        <span key={i} className={`form-chip ${r}`} title={r === "W" ? "Win" : r === "D" ? "Draw" : "Loss"}>
          {r}
        </span>
      ))}
    </div>
  );
}
