export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton skeleton-card" />
      ))}
    </div>
  );
}

const SCAN_STEPS = ["Team form", "Historical results", "Elo ratings", "Goal expectancy"];

export function AiScanningState({ label = "AI analyzing match..." }: { label?: string }) {
  return (
    <div className="state-card">
      <div className="icon">◈</div>
      <div>{label}</div>
      <ul className="scan-list" style={{ listStyle: "none", padding: 0 }}>
        {SCAN_STEPS.map((step, i) => (
          <li key={step} className={i < SCAN_STEPS.length - 1 ? "done" : "active"}>
            {i < SCAN_STEPS.length - 1 ? "✓" : "●"} {step}
          </li>
        ))}
      </ul>
    </div>
  );
}
