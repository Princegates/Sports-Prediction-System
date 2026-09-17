import type { ReactNode } from "react";
import { Mascot } from "./Mascot";

interface Props {
  icon?: string;
  title: string;
  hint?: ReactNode;
}

export function EmptyState({ title, hint }: Props) {
  return (
    <div className="state-card">
      <Mascot pose="sad" size={72} />
      <div style={{ marginTop: 12 }}>{title}</div>
      {hint && <div style={{ marginTop: 8, fontSize: 12.5 }}>{hint}</div>}
    </div>
  );
}
