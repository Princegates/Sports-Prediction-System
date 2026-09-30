/** Applies Carbon's own theme classes -- the same ones its <Theme> React
 * component would add -- at the document level. Used both before React
 * mounts (main.tsx, so there's no flash of the wrong theme) and from
 * ThemeToggle whenever a visitor flips day/night.
 *
 * "white" is Carbon's light theme; "g100" is the darkest and most
 * saturated of its two dark themes, closest to this product's original
 * near-black look, used for anything that isn't explicitly "light".
 */
export function applyCarbonTheme(mode: string): void {
  const isDark = mode !== "light";
  document.body.classList.toggle("cds--g100", isDark);
  document.body.classList.toggle("cds--white", !isDark);
}
