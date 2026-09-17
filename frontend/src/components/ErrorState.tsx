interface Props {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: Props) {
  return (
    <div className="state-card">
      <div className="icon">⚠</div>
      <div>Some match data is temporarily unavailable.</div>
      <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-muted)" }}>{message}</div>
      {onRetry && (
        <button className="btn ghost" style={{ marginTop: 16 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
