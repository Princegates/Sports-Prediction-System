export interface AccentProfile {
  id: string;
  label: string;
  swatch: string;
}

// Curated for maximum visual distinction: evenly spaced around the hue
// wheel (teal -> pink), skipping the red/orange/green range entirely since
// those hues are reserved for status colors (good/warning/serious/critical)
// and never repurposed for branding. Fewer, clearly different options beat
// a long list where adjacent choices are barely distinguishable.
export const ACCENT_PROFILES: AccentProfile[] = [
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "cyan", label: "Cyan", swatch: "#06b6d4" },
  { id: "ocean", label: "Ocean", swatch: "#3987e5" },
  { id: "indigo", label: "Indigo", swatch: "#6366f1" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "berry", label: "Berry", swatch: "#c026d3" },
  { id: "blush", label: "Blush", swatch: "#f472b6" },
  { id: "steel", label: "Steel", swatch: "#64748b" },
];
