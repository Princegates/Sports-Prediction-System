import { useMemo } from "react";
import { MultiSelectPicker, type PickerPreset } from "./MultiSelectPicker";
import type { MarketSummary } from "../types";
import { marketLabel } from "../lib/marketLabel";

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
  /** True while `markets` itself is still loading -- an empty `markets`
   * reads identically whether nothing has been priced yet or the catalog
   * fetch just hasn't resolved (a cold-starting free-tier backend can take
   * 30-50s), so without this an in-flight load looks exactly like "no
   * data", not "loading". */
  loading?: boolean;
}

const MAIN_MARKETS = ["Match Result", "Double Chance", "Both Teams To Score", "Draw No Bet"];

const CATEGORY_ORDER = ["Main", "Total goals", "Team goals & clean sheets", "Corners", "Scores & margins", "Combos", "Half-time"];

function categoryOf(market: string): string {
  if (/^(HT|2H)\b/.test(market) || market === "Half With Most Goals") return "Half-time";
  if (market.includes(" & ") || market.includes(" or ")) return "Combos";
  if (MAIN_MARKETS.includes(market)) return "Main";
  if (market.startsWith("Total Goals")) return "Total goals";
  if (market.startsWith("Total Corners") || market === "Most Corners") return "Corners";
  if (market.startsWith("Home ") || market.startsWith("Away ") || market.startsWith("Both Teams Clean")) {
    return "Team goals & clean sheets";
  }
  return "Scores & margins";
}

export function MarketPicker({
  markets, selected, onChange, presets = [], emptyLabel = "Choose markets", loading = false,
}: Props) {
  const items = useMemo(
    () => markets.map((m) => ({ key: m.market, label: marketLabel(m.market), group: categoryOf(m.market) })),
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
      searchPlaceholder={loading ? "Loading markets…" : `Search ${markets.length} markets`}
      ariaLabel="Choose markets"
      noItemsLabel={
        loading
          ? "Loading markets -- the backend can take up to a minute to wake up if it's been idle."
          : "No upcoming match has been priced yet."
      }
    />
  );
}
