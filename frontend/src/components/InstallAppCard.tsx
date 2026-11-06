import { useEffect, useState, useSyncExternalStore } from "react";
import { canPromptInstall, isIOS, isStandalone, promptInstall, subscribeInstallState } from "../lib/pwa";

const DISMISS_KEY = "install_card_dismissed";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * "Install the app" -- a button where the browser offers installation
 * (Android, Windows, ChromeOS), and the manual steps on iPhone/iPad. Renders
 * nothing when already running as the installed app, or where the browser
 * gives no way to install (so it never shows a button that does nothing).
 *
 * ``dismissible`` adds a close button that stays dismissed; the Profile page
 * uses the permanent version, so someone who closed it can still find it.
 */
export function InstallAppCard({ dismissible = false }: { dismissible?: boolean }) {
  const offered = useSyncExternalStore(subscribeInstallState, canPromptInstall, () => false);
  const [dismissed, setDismissed] = useState(readDismissed);
  const [installed, setInstalled] = useState(false);
  const [standalone, setStandalone] = useState(false);

  useEffect(() => setStandalone(isStandalone()), []);

  const ios = isIOS();
  if (standalone || installed || (dismissible && dismissed)) return null;
  if (!offered && !ios) return null;

  async function handleInstall() {
    const outcome = await promptInstall();
    if (outcome === "accepted") setInstalled(true);
  }

  function dismiss() {
    setDismissed(true);
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // storage disabled -- it just comes back next visit
    }
  }

  return (
    <div className="card card-pad install-card" style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h3 style={{ marginTop: 0, marginBottom: 6, fontSize: 14 }}>Install the app</h3>
          {offered ? (
            <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "var(--text-secondary)" }}>
              Add Socca Intelligence to your home screen or desktop. It opens full screen, like any other app.
            </p>
          ) : (
            <p style={{ margin: 0, fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              On iPhone or iPad: tap the <strong>Share</strong> button in Safari, then <strong>Add to Home Screen</strong>.
              It opens full screen, like any other app.
            </p>
          )}
          {offered && (
            <button className="btn" type="button" onClick={handleInstall}>
              Install app
            </button>
          )}
        </div>
        {dismissible && (
          <button className="btn ghost" type="button" onClick={dismiss} aria-label="Dismiss">
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
