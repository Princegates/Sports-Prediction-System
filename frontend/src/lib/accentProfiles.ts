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
export const ACCENT_PROFILES: AccentProfile[] = [
  { id: "sunset", label: "Sunset", swatch: "#ff6115" },
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "berry", label: "Berry", swatch: "#c026d3" },
  { id: "blush", label: "Blush", swatch: "#f472b6" },
  { id: "steel", label: "Steel", swatch: "#64748b" },
];
