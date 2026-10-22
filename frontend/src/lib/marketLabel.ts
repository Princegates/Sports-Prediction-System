/**
 * How a market's name is shown. Internally the 3-way handicap is a signed
 * line on the home side ("Handicap -1", "Handicap +2") -- that's the key
 * stored on picks and slips, so it doesn't change. On screen it uses
 * SportyBet's own notation instead, a head start in goals as home:away:
 * "Handicap -1" (home gives up a goal) is "Handicap 0:1", "Handicap +2" is
 * "Handicap 2:0" -- the same thing the booked slip shows.
 *
 * The single-half markets are keyed "HT Total Goals 1.5" / "2H Result" but
 * shown as SportyBet names them, "1st Half" / "2nd Half". (Keys that combine
 * the halves with something else -- "HT/FT", "HT + 2H Result" -- stay as is.)
 */
export function marketLabel(market: string): string {
  const m = /^Handicap ([+-])(\d+)$/.exec(market);
  if (m) return m[1] === "-" ? `Handicap 0:${m[2]}` : `Handicap ${m[2]}:0`;
  const half = /^(HT|2H) (Result|Double Chance|Both Teams To Score|Total Goals.*|Correct Score|Exact Goals|Multigoals)$/.exec(market);
  if (half) return `${half[1] === "HT" ? "1st Half" : "2nd Half"} ${half[2]}`;
  return market;
}
