/**
 * The AI Generation promo video on the public landing page.
 *
 * To publish it, add the files to ``frontend/public/videos/`` and redeploy --
 * nothing else needs changing. Until the video file exists the section simply
 * doesn't appear (see AIGenerationVideo.tsx), so this ships safely before it.
 *
 *   ai-generation.mp4          H.264 + AAC, 720p or 1080p. Keep it under 25 MB:
 *                              that is Cloudflare Pages' limit for one file, and
 *                              a visitor on mobile data will thank you.
 *   ai-generation-poster.jpg   optional still shown before play (16:9)
 *   ai-generation.en.vtt       optional captions -- set ``captions`` below
 *
 * Files are served from the site itself on purpose: the site's content
 * security policy only lets media load from its own origin, so a link to
 * YouTube or another host would be blocked.
 */
export const AI_GENERATION_VIDEO = {
  src: "/videos/ai-generation.mp4",
  poster: "/videos/ai-generation-poster.jpg",
  /** e.g. "/videos/ai-generation.en.vtt" -- captions for people who can't hear it. */
  captions: null as string | null,
};
