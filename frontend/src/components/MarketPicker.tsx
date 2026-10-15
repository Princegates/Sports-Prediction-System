import { useMemo } from "react";
import { MultiSelectPicker, type PickerPreset } from "./MultiSelectPicker";
import type { MarketSummary } from "../types";

/** The Markets page's market dropdown, built on the generic MultiSelectPicker
 * -- every market the system prices, any number of them ticked at once. */

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

const CATEGORY_ORDER = ["Main", "Total goals", "Team goals & clean sheets", "Corners", "Scores & margins", "Combos", "Half-time"];

function categoryOf(market: string): string {
  if (/^HT\b/.test(market) || market === "Half With Most Goals") return "Half-time";
  if (market.includes(" & ")) return "Combos";
  if (MAIN_MARKETS.includes(market)) return "Main";
  if (market.startsWith("Total Goals")) return "Total goals";
  if (market.startsWith("Total Corners") || market === "Most Corners") return "Corners";
  if (market.startsWith("Home ") || market.startsWith("Away ") || market.startsWith("Both Teams Clean")) {
    return "Team goals & clean sheets";
  }
  return "Scores & margins";
}

export function MarketPicker({ markets, selected, onChange, presets = [], emptyLabel = "Choose markets" }: Props) {
  const items = useMemo(
    () => markets.map((m) => ({ key: m.market, label: m.market, group: categoryOf(m.market) })),
    [markets],
  );
  const pickerPresets: PickerPreset[] = useMemo(() => presets.map((p) => ({ label: p.label, keys: p.markets })), [presets]);

  return (
    <MultiSelectPicker
      items={items}
      groupOrder={CATEGORY_ORDER}
      selected={selected}
      onChange={onChange}
      presets={pickerPresets}
      emptyLabel={emptyLabel}
      searchPlaceholder={`Search ${markets.length} markets`}
      ariaLabel="Choose markets"
      noItemsLabel="No upcoming match has been priced yet."
    />
  );
}
