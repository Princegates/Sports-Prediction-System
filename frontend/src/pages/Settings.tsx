import { useEffect, useState } from "react";
import { fetchAdminSettings, updateAdminSettings } from "../api";
import { ErrorState } from "../components/ErrorState";
import type { AdminSettings } from "../types";

/**
 * Platform-wide defaults a Super Admin can actually change the behavior of --
 * deliberately small. The skill spec this page comes from lists several more
 * (notification preferences, timezone, "football data preferences"), but
 * none of those have any backend behind them yet, and a toggle that does
 * nothing is worse than no toggle. Appearance is already per-account (see
 * Profile / the theme and accent pickers in the app shell), so it isn't
 * duplicated here as a "platform" setting.
 */
export function Settings() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [durationDays, setDurationDays] = useState("30");
  const [redemptionLimit, setRedemptionLimit] = useState("1");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  function load() {
    setError(null);
    fetchAdminSettings()
      .then((s) => {
        setSettings(s);
        setDurationDays(String(s.default_duration_days));
        setRedemptionLimit(String(s.default_redemption_limit));
      })
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  }

  useEffect(load, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      const updated = await updateAdminSettings({
        default_duration_days: Number(durationDays),
        default_redemption_limit: Number(redemptionLimit),
      });
      setSettings(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && settings === null) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="section-header">
        <h2>Settings</h2>
        <span className="meta">Platform defaults used when generating a new access code</span>
      </div>

      <div className="card card-pad" style={{ maxWidth: 420 }}>
        {settings === null ? (
          <p>Loading...</p>
        ) : (
          <form onSubmit={handleSubmit} className="auth-form">
            <label>
              Default access duration (days)
              <input
                type="number"
                min={1}
                required
                value={durationDays}
                onChange={(e) => { setDurationDays(e.target.value); setSaved(false); }}
              />
            </label>
            <label>
              Default redemption limit
              <input
                type="number"
                min={1}
                required
                value={redemptionLimit}
                onChange={(e) => { setRedemptionLimit(e.target.value); setSaved(false); }}
              />
            </label>

            {error && <p className="auth-error">{error}</p>}
            {saved && !error && <p style={{ fontSize: 12.5, color: "var(--good)", margin: 0 }}>Saved.</p>}

            <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8, alignSelf: "flex-start" }}>
              {busy ? "Saving..." : "Save defaults"}
            </button>

            <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
              Last updated {new Date(settings.updated_at).toLocaleString()}
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
