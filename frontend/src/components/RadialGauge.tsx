import { useEffect, useState } from "react";

interface Props {
  value: number; // 0..1
  size?: number;
  strokeWidth?: number;
  label?: string;
  tone?: "high" | "medium" | "low";
}

const TONE_COLOR: Record<string, string> = {
  high: "var(--good)",
  medium: "var(--warning)",
  low: "var(--critical)",
};

export function RadialGauge({ value, size = 108, strokeWidth = 9, label = "Confidence", tone = "medium" }: Props) {
  const [animated, setAnimated] = useState(0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  useEffect(() => {
    const raf = requestAnimationFrame(() => setAnimated(value));
    return () => cancelAnimationFrame(raf);
  }, [value]);

  const offset = circumference * (1 - animated);
  const color = TONE_COLOR[tone] ?? TONE_COLOR.medium;

  return (
    <div className="radial-gauge" style={{ width: size, height: size }} role="img" aria-label={`${label}: ${Math.round(value * 100)}%`}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-surface-3)"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.9s cubic-bezier(0.22,1,0.36,1)" }}
        />
      </svg>
      <div className="value">
        <strong>{Math.round(value * 100)}%</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}
