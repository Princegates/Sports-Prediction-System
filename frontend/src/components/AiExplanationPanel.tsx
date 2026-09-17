interface Props {
  positive: string[];
  negative: string[];
}

export function AiExplanationPanel({ positive, negative }: Props) {
  if (positive.length === 0 && negative.length === 0) {
    return <p className="badge-neutral">No standout factors identified for this match yet.</p>;
  }

  return (
    <ul className="factor-list">
      {positive.map((text) => (
        <li key={text} className="positive">
          <span className="icon">+</span>
          <span>{text}</span>
        </li>
      ))}
      {negative.map((text) => (
        <li key={text} className="negative">
          <span className="icon">-</span>
          <span>{text}</span>
        </li>
      ))}
    </ul>
  );
}
