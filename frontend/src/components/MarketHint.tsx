import { useId, useState } from "react";

export interface MarketHintLine {
  label: string;
  definition: string;
}

/** A small "i" that shows what a market or outcome means when tapped.
 * Definitions used to live only in hover `title`s, which a phone never
 * shows. Clicks stop here so a hint inside a clickable row doesn't also
 * open the row. Renders nothing when there's nothing to explain. */
export function MarketHint({ name, text }: { name: string; text: string | MarketHintLine[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const lines = typeof text === "string" ? (text.trim() ? [{ label: "", definition: text.trim() }] : []) : text.filter((l) => l.definition);
  if (lines.length === 0) return null;
  return (
    <>
      <button
        type="button"
        className="market-hint-btn"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${open ? "Hide" : "What is"} ${name}?`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <span aria-hidden>i</span>
      </button>
      {open && (
        <span id={id} className="market-hint-text" onClick={(e) => e.stopPropagation()}>
          {lines.map((l, i) => (
            <span key={i} style={{ display: "block" }}>
              {l.label && <strong>{l.label}: </strong>}
              {l.definition}
            </span>
          ))}
        </span>
      )}
    </>
  );
}
