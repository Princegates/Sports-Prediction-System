import { useEffect, useRef, useState } from "react";
import { ACCENT_PROFILES } from "../lib/accentProfiles";

interface Props {
  accent: string;
  onChange: (id: string) => void;
}

export function AccentPicker({ accent, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, []);

  return (
    <div className="accent-picker" ref={ref}>
      <button className="btn ghost accent-picker-trigger" onClick={() => setOpen((o) => !o)} aria-label="Change color profile" aria-expanded={open} title="Color profile">
        <span className="accent-swatch" />
      </button>
      {open && (
        <div className="accent-picker-panel" role="menu">
          <h4>Color profile</h4>
          <div className="accent-grid">
            {ACCENT_PROFILES.map((p) => (
              <button
                key={p.id}
                className={`accent-option${p.id === accent ? " selected" : ""}`}
                style={{ background: p.swatch }}
                title={p.label}
                aria-label={p.label}
                aria-pressed={p.id === accent}
                onClick={() => {
                  onChange(p.id);
                  setOpen(false);
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
