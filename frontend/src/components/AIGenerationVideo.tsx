import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AI_GENERATION_VIDEO } from "../lib/promoVideo";

/** Whether ``url`` is really a file of ``kind`` ("video"/"image"). The host
 * answers an unknown path with the app's own page (a 200 of text/html) so a
 * plain "does it load" check would say a missing file exists -- the content
 * type is what tells them apart. */
async function exists(url: string, kind: "video" | "image"): Promise<boolean> {
  try {
    const response = await fetch(url, { method: "HEAD" });
    return response.ok && (response.headers.get("content-type") ?? "").startsWith(`${kind}/`);
  } catch {
    return false;
  }
}

/**
 * The AI Generation promo on the landing page: a video that plays on click
 * (with sound -- it never autoplays), beside what the tool does and a way
 * into it. Renders nothing until the video file has been added (see
 * lib/promoVideo.ts), so there is never a broken player on the public page.
 */
export function AIGenerationVideo() {
  const { src, poster, captions } = AI_GENERATION_VIDEO;
  const video = useRef<HTMLVideoElement | null>(null);
  const [available, setAvailable] = useState(false);
  const [hasPoster, setHasPoster] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [ended, setEnded] = useState(false);
  // The shipped promo is a vertical (phone) recording, so that is assumed until
  // the file says otherwise -- a widescreen replacement then lays out correctly too.
  const [orientation, setOrientation] = useState<"portrait" | "landscape">("portrait");

  useEffect(() => {
    let cancelled = false;
    exists(src, "video").then((ok) => !cancelled && setAvailable(ok));
    exists(poster, "image").then((ok) => !cancelled && setHasPoster(ok));
    return () => {
      cancelled = true;
    };
  }, [src, poster]);

  if (!available) return null;

  function start() {
    const el = video.current;
    if (!el) return;
    if (ended) el.currentTime = 0;
    void el.play();
  }

  return (
    <section className="landing-section promo-video" data-orientation={orientation} aria-labelledby="promo-video-title">
      <div className="promo-video-copy">
        <span className="promo-video-kicker">AI Generation</span>
        <h2 id="promo-video-title">Build a whole slip in seconds</h2>
        <p>
          Our most-used tool. Tell it the leagues and markets you want and how sure each leg must be, and it assembles
          a slip from the model's own probabilities.
        </p>
        <ul className="promo-video-points">
          <li>Choose leagues, markets and how many legs</li>
          <li>Set the minimum probability every leg has to clear</li>
          <li>Get a booking code for SportyBet and load the slip in one tap</li>
        </ul>
        <div className="promo-video-actions">
          <Link className="btn btn-lg" to="/try-ai-generation">Try AI Generation</Link>
          <Link className="btn ghost btn-lg" to="/pricing">See pricing</Link>
        </div>
        <p className="meta">Probabilities, not guarantees -- no pick is certain to win.</p>
      </div>

      <div className="promo-video-frame">
        <video
          ref={video}
          src={src}
          poster={hasPoster ? poster : undefined}
          controls
          playsInline
          preload="metadata"
          aria-label="Video: how AI Generation builds a slip"
          onLoadedMetadata={(e) =>
            setOrientation(e.currentTarget.videoHeight > e.currentTarget.videoWidth ? "portrait" : "landscape")
          }
          onPlay={() => {
            setPlaying(true);
            setEnded(false);
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false);
            setEnded(true);
          }}
        >
          {captions && <track kind="captions" src={captions} srcLang="en" label="English" default />}
        </video>
        {!playing && (
          <button type="button" className="promo-video-play" onClick={start} aria-label={ended ? "Replay the video" : "Play the video"}>
            <svg viewBox="0 0 24 24" width="30" height="30" aria-hidden>
              {ended ? (
                <path fill="currentColor" d="M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z" />
              ) : (
                <path fill="currentColor" d="M8 5v14l11-7z" />
              )}
            </svg>
          </button>
        )}
      </div>
    </section>
  );
}
