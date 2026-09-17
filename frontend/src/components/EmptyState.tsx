import type { ReactNode } from "react";

interface Props {
  icon?: string;
  title: string;
  hint?: ReactNode;
}

export function EmptyState({ icon = "◌", title, hint }: Props) {
  return (
    <div className="state-card">
      <div className="icon">{icon}</div>
      <div>{title}</div>
      {hint && <div style={{ marginTop: 8, fontSize: 12.5 }}>{hint}</div>}
    </div>
  );
}
