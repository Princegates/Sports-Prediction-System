/** URL building/parsing for a fixture's public preview page.
 *
 * The id is the only thing that actually has to resolve -- it's what the
 * backend looks the match up by. The team names after it are there purely
 * for a readable URL and for search engines, who weigh the words in a URL;
 * they're never read back out. That's also why parsing only needs the
 * leading digits: a stale link whose teams drifted (a correction, a replay
 * with different naming) still resolves instead of 404ing.
 */

function slugifyWord(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // strip accents (Koln -> koln, not k-ln)
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** e.g. {match_id: 482913, home_team: "Manchester United", away_team: "Arsenal"}
 * -> "482913-manchester-united-vs-arsenal" */
export function fixtureSlug(f: { match_id: number; home_team: string; away_team: string }): string {
  const teams = `${slugifyWord(f.home_team)}-vs-${slugifyWord(f.away_team)}`;
  return teams ? `${f.match_id}-${teams}` : String(f.match_id);
}

/** The inverse's only real job: pull the leading id back out of whatever
 * followed it. Returns null for anything that doesn't start with digits,
 * so the page can tell "malformed URL" apart from "valid id, unknown
 * fixture" (a 404 from the API) and word each differently. */
export function parseFixtureId(param: string | undefined): number | null {
  if (!param) return null;
  const match = /^(\d+)/.exec(param);
  if (!match) return null;
  const id = Number(match[1]);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
