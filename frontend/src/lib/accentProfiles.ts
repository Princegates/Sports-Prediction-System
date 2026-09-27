export interface AccentProfile {
  id: string;
  label: string;
  swatch: string;
}

// "sunset" is the flagship brand look (the site's own orange + violet, paired
// with the dark-green/cream base in styles.css) and the default. The rest are
// alternatives for anyone who wants a different accent, curated for maximum
// visual distinction from each other: evenly spaced around the hue wheel
// (teal -> pink), skipping the red/orange/green range since those hues are
// close to the status colors (good/warning/serious/critical) and to sunset
// itself. Fewer, clearly different options beat a long list where adjacent
// choices are barely distinguishable.
export const ACCENT_PROFILES: AccentProfile[] = [
  { id: "sunset", label: "Sunset", swatch: "#ff6115" },
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "cyan", label: "Cyan", swatch: "#06b6d4" },
  { id: "ocean", label: "Ocean", swatch: "#3987e5" },
  { id: "indigo", label: "Indigo", swatch: "#6366f1" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "berry", label: "Berry", swatch: "#c026d3" },
  { id: "blush", label: "Blush", swatch: "#f472b6" },
  { id: "steel", label: "Steel", swatch: "#64748b" },
];
