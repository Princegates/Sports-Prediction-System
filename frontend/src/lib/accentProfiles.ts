export interface AccentProfile {
  id: string;
  label: string;
  swatch: string;
}

// "sunset" is the flagship brand look (the site's own orange, paired with
// the dark-green/cream base in styles.css) and the default. The rest are
// alternatives for anyone who wants a different accent. No blue options
// (ocean/cyan/indigo removed entirely, not just demoted) and every profile
// is a single hue -- styles.css aliases --ai-violet to --accent for all of
// them, so nothing ever blends two colors.
//
// The 10 after steel lean on saturation/lightness rather than hue spacing
// for distinction: with red/orange/green (status colors + sunset) and blue
// both off-limits, the only hue room left is a narrow purple-to-pink arc,
// too narrow to fit 16 total profiles by hue alone.
export const ACCENT_PROFILES: AccentProfile[] = [
  { id: "sunset", label: "Sunset", swatch: "#ff6115" },
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "berry", label: "Berry", swatch: "#c026d3" },
  { id: "blush", label: "Blush", swatch: "#f472b6" },
  { id: "steel", label: "Steel", swatch: "#64748b" },
  { id: "orchid", label: "Orchid", swatch: "#c77dff" },
  { id: "plum", label: "Plum", swatch: "#a873e0" },
  { id: "magenta", label: "Magenta", swatch: "#ea4fc0" },
  { id: "grape", label: "Grape", swatch: "#a378e2" },
  { id: "rose", label: "Rose", swatch: "#ff7a9e" },
  { id: "lilac", label: "Lilac", swatch: "#b3a0e8" },
  { id: "mulberry", label: "Mulberry", swatch: "#d46ba6" },
  { id: "amethyst", label: "Amethyst", swatch: "#b47aec" },
  { id: "wine", label: "Wine", swatch: "#c6789b" },
  { id: "mauve", label: "Mauve", swatch: "#a68a96" },
];
