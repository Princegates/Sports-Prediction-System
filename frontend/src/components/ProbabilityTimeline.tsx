interface Point {
  label: string;
  value: number; // 0..1
}

interface Props {
  points: Point[];
  seriesLabel?: string;
}

const WIDTH = 560;
const HEIGHT = 170;
const PAD_X = 12;
const PAD_TOP = 16;
const PAD_BOTTOM = 28;

export function ProbabilityTimeline({ points, seriesLabel = "Home win probability" }: Props) {
  if (points.length < 2) {
    return <p className="badge-neutral">Not enough snapshots yet to draw a timeline for this match.</p>;
  }

  const plotWidth = WIDTH - PAD_X * 2;
  const plotHeight = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const stepX = plotWidth / (points.length - 1);

  const xy = points.map((p, i) => ({
    x: PAD_X + i * stepX,
    y: PAD_TOP + (1 - p.value) * plotHeight,
    ...p,
  }));

  const linePath = xy.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${xy[xy.length - 1].x.toFixed(1)} ${PAD_TOP + plotHeight} L ${xy[0].x.toFixed(1)} ${PAD_TOP + plotHeight} Z`;

  const showAllLabels = points.length <= 6;

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" height={HEIGHT} role="img" aria-label={seriesLabel}>
        <defs>
          <linearGradient id="timelineFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((frac) => (
          <line
            key={frac}
            x1={PAD_X}
            x2={WIDTH - PAD_X}
            y1={PAD_TOP + frac * plotHeight}
            y2={PAD_TOP + frac * plotHeight}
            stroke="var(--gridline)"
            strokeWidth={1}
          />
        ))}

        <path d={areaPath} fill="url(#timelineFill)" stroke="none" />
        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth={2} />

        {xy.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={4} fill="var(--bg-surface)" stroke="var(--accent-strong)" strokeWidth={2}>
              <title>{`${p.label}: ${(p.value * 100).toFixed(0)}%`}</title>
            </circle>
            {(showAllLabels || i === 0 || i === xy.length - 1) && (
              <text x={p.x} y={HEIGHT - 8} fontSize="9.5" fill="var(--text-muted)" textAnchor={i === 0 ? "start" : i === xy.length - 1 ? "end" : "middle"}>
                {p.label}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}
