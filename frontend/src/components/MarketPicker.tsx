import { useEffect, useMemo, useRef, useState } from "react";
import type { MarketSummary } from "../types";

/**
 * The Markets page's market dropdown: every market the system prices, any
 * number of them ticked at once. A native `<select multiple>` would do the
 * job on paper, but it needs Ctrl/Cmd-click on desktop and renders as a
 * cramped list box -- checkboxes in a popover are what people expect from a
 * "pick several" dropdown.
 *
 * Ticks are kept as a draft while the panel is open and handed to
 * `onChange` once when it closes, so ticking five markets costs the page
 * one reload rather than five.
 */

export interface MarketPreset {
  label: string;
  markets: string[];
}

interface Props {
  markets: MarketSummary[];
  selected: string[];
  onChange: (next: string[]) => void;
  presets?: MarketPreset[];
  /** What the button says with nothing ticked -- the list view treats that
   * as "every market", the coupon as "choose some". */
  emptyLabel?: string;
}

const MAIN_MARKETS = ["Match Result", "Double Chance", "Both Teams To Score", "Draw No Bet"];

const CATEGORY_ORDER = ["Main", "Total goals", "Team goals & clean sheets", "Scores & margins", "Combos", "Half-time"];

function categoryOf(market: string): string {
  if (/^HT\b/.test(market) || market === "Half With Most Goals") return "Half-time";
  if (market.includes(" & ")) return "Combos";
  if (MAIN_MARKETS.includes(market)) return "Main";
  if (market.startsWith("Total Goals")) return "Total goals";
  if (market.startsWith("Home ") || market.startsWith("Away ") || market.startsWith("Both Teams Clean")) {
    return "Team goals & clean sheets";
  }
  return "Scores & margins";
}

function summarize(selected: string[], emptyLabel: string): string {
  if (selected.length === 0) return emptyLabel;
  if (selected.length <= 2) return selected.join(", ");
  return `${selected[0]} + ${selected.length - 1} more`;
}

export function MarketPicker({ markets, selected, onChange, presets = [], emptyLabel = "Choose markets" }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>(selected);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  const order = useMemo(() => new Map(markets.map((m, i) => [m.market, i])), [markets]);

  function openPanel() {
    setDraft(selected);
    setQuery("");
    setOpen(true);
  }

  function close() {
    setOpen(false);
    const next = draftRef.current;
    if (next.length !== selected.length || next.some((m, i) => m !== selected[i])) onChange(next);
  }

  // Clicking anywhere outside, or Escape, closes the panel -- and applies
  // the ticks, the same as Done does.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
    // close() reads the draft through a ref, so it's safe to leave out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, selected]);

  /** Kept in the catalog's own order, so the coupon's columns don't depend
   * on the order boxes happened to be ticked in. */
  function sortByCatalog(list: string[]): string[] {
    return [...list].sort((a, b) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  }

  function toggle(market: string) {
    setDraft((d) => (d.includes(market) ? d.filter((m) => m !== market) : sortByCatalog([...d, market])));
  }

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const visible = q ? markets.filter((m) => m.market.toLowerCase().includes(q)) : markets;
    const byCategory = new Map<string, MarketSummary[]>();
    for (const m of visible) {
      const cat = categoryOf(m.market);
      if (!byCategory.has(cat)) byCategory.set(cat, []);
      byCategory.get(cat)!.push(m);
    }
    return CATEGORY_ORDER.filter((c) => byCategory.has(c)).map((c) => [c, byCategory.get(c)!] as const);
  }, [markets, query]);

  return (
    <div className="market-picker" ref={rootRef}>
      <button
        type="button"
        className="filter-select market-picker-toggle"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? close() : openPanel())}
      >
        <span className="market-picker-summary">{summarize(selected, emptyLabel)}</span>
        <span aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="market-picker-panel" role="dialog" aria-label="Choose markets">
          <input
            type="search"
            className="filter-select market-picker-search"
            placeholder={`Search ${markets.length} markets`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />

          {presets.length > 0 && !query && (
            <div className="market-picker-presets">
              {presets.map((p) => {
                const active = p.markets.length === draft.length && p.markets.every((m) => draft.includes(m));
                return (
                  <button
                    key={p.label}
                    type="button"
                    className={`filter-chip${active ? " active" : ""}`}
                    onClick={() => setDraft(sortByCatalog(p.markets.filter((m) => order.has(m))))}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          )}

          <div className="market-picker-list">
            {groups.length === 0 && (
              <p className="sub">{query ? `No market matches “${query}”.` : "No upcoming match has been priced yet."}</p>
            )}
            {groups.map(([category, items]) => (
              <fieldset key={category} className="market-picker-group">
                <legend>{category}</legend>
                {items.map((m) => (
                  <label key={m.market} className="market-picker-option">
                    <input type="checkbox" checked={draft.includes(m.market)} onChange={() => toggle(m.market)} />
                    <span>{m.market}</span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>

          <div className="market-picker-footer">
            <span className="sub">
              {draft.length} selected
            </span>
            <button type="button" className="btn ghost" onClick={() => setDraft([])} disabled={draft.length === 0}>
              Clear
            </button>
            <button type="button" className="btn" onClick={close}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
