import { useEffect, useRef, useState } from "react";
import { fetchBanner, reportAdClick, reportAdImpression } from "../api";
import type { BannerSlide } from "../types";

const DISMISSED_KEY = "news_banner_dismissed";
const SEEN_KEY = "news_banner_ads_seen";
const REFRESH_MS = 10 * 60 * 1000;
// Seconds each slide takes to cross the strip -- the loop's length follows the slide count.
const SECONDS_PER_SLIDE = 9;

function readSession(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null; // private mode / blocked storage -- the banner just doesn't remember
  }
}

function writeSession(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // ignore
  }
}

function adId(slide: BannerSlide): number | null {
  return slide.kind === "ad" ? Number(slide.id.split(":")[1]) : null;
}

function SlideLink({ slide, hidden }: { slide: BannerSlide; hidden?: boolean }) {
  const ad = slide.kind === "ad";
  return (
    <a
      className={`nb-slide${ad ? " nb-ad" : ""}`}
      href={slide.url}
      target="_blank"
      // "sponsored" tells search engines this is a paid link; noopener/noreferrer
      // keep the destination from reaching back into this tab.
      rel={ad ? "sponsored noopener noreferrer" : "noopener noreferrer"}
      tabIndex={hidden ? -1 : undefined}
      data-ad-id={ad && !hidden ? adId(slide) ?? undefined : undefined}
      title={slide.body ?? undefined}
      onClick={() => {
        const id = adId(slide);
        if (id !== null) void reportAdClick(id);
      }}
    >
      {ad ? <span className="nb-chip">Sponsored</span> : null}
      <span className="nb-label">{slide.label}</span>
      <span className="nb-headline">{slide.headline}</span>
      {ad && slide.cta ? <span className="nb-cta">{slide.cta}</span> : null}
    </a>
  );
}

/**
 * A slim strip under the top bar that scrolls football headlines -- and, when
 * an admin has switched any on, clearly labelled sponsored slides -- past the
 * member. It pauses on hover or keyboard focus, and under "reduce motion" it
 * stops animating and becomes a row the member scrolls themselves.
 *
 * Both kinds of slide are the same shape on purpose (see app/banner.py):
 * this is the app's one slot for third-party messages as well as for news.
 * Renders nothing when there is nothing to show, and can be dismissed for the
 * rest of the visit.
 */
export function NewsBanner() {
  const [slides, setSlides] = useState<BannerSlide[]>([]);
  const [dismissed, setDismissed] = useState(() => readSession(DISMISSED_KEY) === "1");
  const viewport = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (dismissed) return;
    let cancelled = false;
    const load = () =>
      fetchBanner()
        .then((r) => !cancelled && setSlides(r.slides))
        .catch(() => {
          // The banner is a nicety: a failed fetch leaves whatever it already showed.
        });
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [dismissed]);

  // An ad counts as shown once, per visit, when it actually scrolls into view.
  useEffect(() => {
    const root = viewport.current;
    if (!root || slides.length === 0 || typeof IntersectionObserver === "undefined") return;
    const seen = new Set((readSession(SEEN_KEY) ?? "").split(",").filter(Boolean));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.adId;
          if (!entry.isIntersecting || !id || seen.has(id)) continue;
          seen.add(id);
          writeSession(SEEN_KEY, [...seen].join(","));
          void reportAdImpression(Number(id));
        }
      },
      { root, threshold: 0.6 },
    );
    root.querySelectorAll("[data-ad-id]").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [slides]);

  if (dismissed || slides.length === 0) return null;

  return (
    <section className="news-banner" aria-label="Football news">
      <span className="nb-title" aria-hidden>Football news</span>
      <div className="nb-viewport" ref={viewport}>
        <div className="nb-track" style={{ animationDuration: `${Math.max(30, slides.length * SECONDS_PER_SLIDE)}s` }}>
          {slides.map((s) => (
            <SlideLink key={s.id} slide={s} />
          ))}
          {/* A second copy makes the loop seamless; hidden from assistive tech and the tab order. */}
          <span className="nb-copy" aria-hidden>
            {slides.map((s) => (
              <SlideLink key={`copy-${s.id}`} slide={s} hidden />
            ))}
          </span>
        </div>
      </div>
      <button
        type="button"
        className="nb-close"
        aria-label="Hide the news banner"
        onClick={() => {
          setDismissed(true);
          writeSession(DISMISSED_KEY, "1");
        }}
      >
        ×
      </button>
    </section>
  );
}
