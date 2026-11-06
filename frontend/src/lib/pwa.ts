/**
 * Installable-app plumbing: registers the service worker and keeps hold of
 * the browser's "install" offer so a button anywhere in the app can use it.
 *
 * Chrome, Edge and Samsung Internet (Android, Windows, ChromeOS) fire
 * `beforeinstallprompt` once, early -- often before React has mounted -- so
 * the event is captured here at module load rather than inside a component.
 * Safari on iPhone/iPad has no such event; installing there is the manual
 * Share -> Add to Home Screen, which we can only describe, not trigger.
 */

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

export function registerServiceWorker(): void {
  // Only the production build: in dev, a worker would serve stale files while
  // you edit them.
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Private mode, blocked storage, an old browser -- the site works the same without it.
    });
  });
}

export function listenForInstallOffer(): void {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // hold it for our own button instead of the browser's mini-bar
    deferred = event as BeforeInstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    notify();
  });
}

export function subscribeInstallState(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function canPromptInstall(): boolean {
  return deferred !== null;
}

/** True when running as the installed app rather than in a browser tab. */
export function isStandalone(): boolean {
  try {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      (navigator as Navigator & { standalone?: boolean }).standalone === true
    );
  } catch {
    return false;
  }
}

/** iPhone/iPad (including iPadOS, which reports itself as a Mac with a touch screen). */
export function isIOS(): boolean {
  const ua = navigator.userAgent;
  return /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1);
}

export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  if (!deferred) return "unavailable";
  const offer = deferred;
  deferred = null; // an offer can only be used once
  notify();
  await offer.prompt();
  const { outcome } = await offer.userChoice;
  return outcome;
}
