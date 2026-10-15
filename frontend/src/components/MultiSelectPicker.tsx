import { useEffect, useMemo, useRef, useState } from "react";

/**
 * The popover behind MarketPicker and LeaguePicker: tick any number of named
 * items, grouped, with a search box and a running summary on the toggle
 * button. A native `<select multiple>` would do the job on paper, but it
 * needs Ctrl/Cmd-click on desktop and renders as a cramped list box --
 * checkboxes in a popover are what people expect from a "pick several"
 * dropdown.
 *
 * Ticks are kept as a draft while the panel is open and handed to
 * `onChange` once when it closes, so ticking five items costs the page one
 * reload rather than five.
 */

export interface PickerItem {
  key: string;
  label: string;
  group: string;
}

export interface PickerPreset {
  label: string;
  keys: string[];
}

interface Props {
  items: PickerItem[];
  groupOrder: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  presets?: PickerPreset[];
  /** What the button says with nothing ticked. */
  emptyLabel: string;
  searchPlaceholder: string;
  ariaLabel: string;
  noItemsLabel: string;
}

function summarize(selected: string[], labelOf: Map<string, string>, emptyLabel: string): string {
  if (selected.length === 0) return emptyLabel;
  const label = (k: string) => labelOf.get(k) ?? k;
  if (selected.length <= 2) return selected.map(label).join(", ");
  return `${label(selected[0])} + ${selected.length - 1} more`;
}

export function MultiSelectPicker({
  items,
  groupOrder,
  selected,
  onChange,
  presets = [],
  emptyLabel,
  searchPlaceholder,
  ariaLabel,
  noItemsLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>(selected);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  const order = useMemo(() => new Map(items.map((it, i) => [it.key, i])), [items]);
  const labelOf = useMemo(() => new Map(items.map((it) => [it.key, it.label])), [items]);

  function openPanel() {
    setDraft(selected);
    setQuery("");
    setOpen(true);
  }

  function close() {
    setOpen(false);
    const next = draftRef.current;
    if (next.length !== selected.length || next.some((k, i) => k !== selected[i])) onChange(next);
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

  /** Kept in the catalog's own order, so columns/results don't depend on
   * the order boxes happened to be ticked in. */
  function sortByCatalog(list: string[]): string[] {
    return [...list].sort((a, b) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  }

  function toggle(key: string) {
    setDraft((d) => (d.includes(key) ? d.filter((k) => k !== key) : sortByCatalog([...d, key])));
  }

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const visible = q ? items.filter((it) => it.label.toLowerCase().includes(q)) : items;
    const byGroup = new Map<string, PickerItem[]>();
    for (const it of visible) {
      if (!byGroup.has(it.group)) byGroup.set(it.group, []);
      byGroup.get(it.group)!.push(it);
    }
    return groupOrder.filter((g) => byGroup.has(g)).map((g) => [g, byGroup.get(g)!] as const);
  }, [items, groupOrder, query]);

  return (
    <div className="market-picker" ref={rootRef}>
      <button
        type="button"
        className="filter-select market-picker-toggle"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? close() : openPanel())}
      >
        <span className="market-picker-summary">{summarize(selected, labelOf, emptyLabel)}</span>
        <span aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="market-picker-panel" role="dialog" aria-label={ariaLabel}>
          <input
            type="search"
            className="filter-select market-picker-search"
            placeholder={searchPlaceholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />

          {presets.length > 0 && !query && (
            <div className="market-picker-presets">
              {presets.map((p) => {
                const active = p.keys.length === draft.length && p.keys.every((k) => draft.includes(k));
                return (
                  <button
                    key={p.label}
                    type="button"
                    className={`filter-chip${active ? " active" : ""}`}
                    onClick={() => setDraft(sortByCatalog(p.keys.filter((k) => order.has(k))))}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          )}

          <div className="market-picker-list">
            {groups.length === 0 && (
              <p className="sub">{query ? `No match for “${query}”.` : noItemsLabel}</p>
            )}
            {groups.map(([group, groupItems]) => (
              <fieldset key={group} className="market-picker-group">
                <legend>{group}</legend>
                {groupItems.map((it) => (
                  <label key={it.key} className="market-picker-option">
                    <input type="checkbox" checked={draft.includes(it.key)} onChange={() => toggle(it.key)} />
                    <span>{it.label}</span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>

          <div className="market-picker-footer">
            <span className="sub">{draft.length} selected</span>
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
