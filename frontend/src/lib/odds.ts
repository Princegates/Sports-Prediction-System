/** A probability as people actually feel it: "about 1 in 14" for a combo
 * that rarely lands, a percentage once it's at least even. Shown next to
 * combined odds so the payout never appears without the chance of getting
 * it -- accumulators multiply the bookmaker's margin with every leg, and
 * "7.4x" on its own reads far better than it is. */
export function chanceLabel(probability: number): string {
  if (!(probability > 0)) return "a very small chance";
  if (probability >= 0.5) return `about ${Math.round(probability * 100)}%`;
  const n = Math.round(1 / probability);
  return n >= 1000 ? "under 1 in 1,000" : `about 1 in ${n}`;
}
