import { useState } from "react";

// Words that would otherwise win the initials ("FC Barcelona" -> "B", not "FB").
const FILLER = new Set(["fc", "afc", "cf", "sc", "ac", "as", "fk", "sk", "if", "bk", "cd", "ud", "sv", "vfb", "vfl", "1."]);

function initials(name: string): string {
  const words = name.split(/[\s/-]+/).filter((w) => w && !FILLER.has(w.toLowerCase()));
  const picked = (words.length ? words : name.split(/\s+/)).slice(0, 2);
  return picked.map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";
}

/** Stable per-club hue, so a club's fallback badge looks the same everywhere. */
function hue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
}

/** A club's crest, or a lettered badge when there's no crest yet (its
 * provider id isn't known) or the image fails to load. Decorative -- the
 * club name is always printed beside it -- so it's hidden from screen
 * readers. */
export function TeamCrest({ name, logoUrl, size = 20 }: { name: string; logoUrl?: string | null; size?: number }) {
  const [failed, setFailed] = useState(false);

  if (logoUrl && !failed) {
    return (
      <img
        className="team-crest"
        src={logoUrl}
        alt=""
        aria-hidden
        width={size}
        height={size}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span
      className="team-crest team-crest-fallback"
      aria-hidden
      style={{ width: size, height: size, fontSize: Math.round(size * 0.42), background: `hsl(${hue(name)} 45% 42%)` }}
    >
      {initials(name)}
    </span>
  );
}
