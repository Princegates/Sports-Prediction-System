export interface AccentProfile {
  id: string;
  label: string;
  swatch: string;
}

// "sunset" is the flagship brand look (the site's own orange) and the
// default. Every id here must have a matching `body[data-accent="id"]`
// block in custom.scss -- that's what actually recolors the site; this
// list only drives the Settings > Appearance dropdown and its swatches.
// Each profile is a single hue: styles.css aliases --ai-violet to --accent
// for all of them, so nothing ever blends two colors, and --accent-soft/
// --accent-strong are derived automatically from --accent (see custom.scss)
// rather than hand-picked per profile.
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
  { id: "cobalt", label: "Cobalt", swatch: "#2f6fed" },
  { id: "indigo", label: "Indigo", swatch: "#6366f1" },
  { id: "cyan", label: "Cyan", swatch: "#22d3ee" },
  { id: "denim", label: "Denim", swatch: "#3b6ea5" },
];
