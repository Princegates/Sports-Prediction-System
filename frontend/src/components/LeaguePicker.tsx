import { useMemo } from "react";
import { MultiSelectPicker, type PickerPreset } from "./MultiSelectPicker";

/** Markets' and Predictions' league filter: pick any number of leagues, not
 * just one or all. The topbar's own league select (AppShell's
 * LeagueContext) only ever holds a single value, which is enough for pages
 * that just highlight one league at a time -- these two pages let people
 * build a watchlist instead ("show me these three leagues"), so they keep
 * their own selection rather than being limited to the topbar's. */

const MAIN_LEAGUES = new Set([
  "English Premier League",
  "Spanish La Liga",
  "German Bundesliga",
  "Italian Serie A",
  "French Ligue 1",
  "Dutch Eredivisie",
  "Portuguese Primeira Liga",
  "Turkish Süper Lig",
  "UEFA Champions League",
  "UEFA Europa League",
  "English Championship",
]);

const GROUP_ORDER = ["Main leagues", "Other leagues"];

interface Props {
  leagues: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  emptyLabel?: string;
  presets?: PickerPreset[];
}

export function LeaguePicker({ leagues, selected, onChange, emptyLabel = "All leagues", presets = [] }: Props) {
  const items = useMemo(
    () => leagues.map((l) => ({ key: l, label: l, group: MAIN_LEAGUES.has(l) ? "Main leagues" : "Other leagues" })),
    [leagues],
  );

  return (
    <MultiSelectPicker
      items={items}
      groupOrder={GROUP_ORDER}
      selected={selected}
      onChange={onChange}
      presets={presets}
      emptyLabel={emptyLabel}
      searchPlaceholder={`Search ${leagues.length} leagues`}
      ariaLabel="Choose leagues"
      noItemsLabel="No leagues available."
    />
  );
}

/** Display text for a selected-leagues array, including the "all leagues"
 * sentinel (an empty array). */
export function describeLeagues(selected: string[]): string {
  if (selected.length === 0) return "all leagues";
  if (selected.length === 1) return selected[0];
  return `${selected.length} leagues`;
}
