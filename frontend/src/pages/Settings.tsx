import { useEffect, useMemo, useState } from "react";
import { fetchSettings, fetchSystemStatus, saveSettings, sendTestEmail } from "../api";
import { ACCENT_PROFILES } from "../lib/accentProfiles";
import { ErrorState } from "../components/ErrorState";
import type { SettingSpec, SettingsPayload, SystemStatus } from "../types";

type Draft = Record<string, string | number | boolean>;

/** Groups in the order an operator actually needs them: turn email on, set
 *  how access works, make it look right, and only then touch the model. */
const GROUP_ORDER = ["data", "email", "access", "appearance", "notice", "picks", "model", "betcode"];

const GROUP_NOTES: Record<string, string> = {
  data:
    "Optional. openfootball supplies the history for free and without limits; a key here is spent only on what it cannot do — European competitions, lineups, injuries and market odds.",
  email:
    "Leave the host blank to keep email off — access codes still work, you just send them yourself.",
  access: "Defaults for issuing codes, and whether new people can sign up at all.",
  appearance: "What visitors see before they've chosen anything of their own.",
  notice: "A dismissible banner shown on the Dashboard to every signed-in account — for an outage, a new league going live, or anything else worth a heads-up.",
  picks: "Feature outcomes from a match's Markets tab to promote them on every Dashboard.",
  model:
    "Only a fallback. Once a league has been backtested, its weights are fitted from that league's own validation data and those are used instead — these apply to leagues that haven't been trained yet.",
  betcode:
    "Provider = none still lets people build and preview selections at a target price — only the redeemable code and the deep link into the sportsbook need an aggregator.",
};

function relative(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const hours = (Date.now() - then) / 3_600_000;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function Settings() {
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  function load() {
    fetchSettings()
      .then((p) => {
        setPayload(p);
        setDraft({});
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
    fetchSystemStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }

  useEffect(load, []);

  const dirty = useMemo(() => Object.keys(draft).length > 0, [draft]);

  function valueOf(spec: SettingSpec): string | number | boolean {
    if (spec.key in draft) return draft[spec.key];
    return payload?.values[spec.key] ?? "";
  }

  async function handleSave() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const next = await saveSettings({ values: draft });
      setPayload(next);
      setDraft({});
      setSaved(`Saved ${Object.keys(draft).length} change${Object.keys(draft).length === 1 ? "" : "s"}.`);
      fetchSystemStatus().then(setStatus).catch(() => {});
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function handleReset(key: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await saveSettings({ reset: [key] });
      setPayload(next);
      setDraft((d) => {
        const { [key]: _drop, ...rest } = d;
        return rest;
      });
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function handleTestEmail() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await sendTestEmail();
      setTestResult(result.detail);
    } catch (e) {
      setTestResult(String(e instanceof Error ? e.message : e));
    } finally {
      setTesting(false);
    }
  }

  if (error && !payload) return <ErrorState message={error} />;
  if (!payload) return <p className="badge-neutral">Loading settings…</p>;

  function renderField(spec: SettingSpec) {
    const current = valueOf(spec);
    const overridden = payload!.overridden.includes(spec.key);
    const set = (v: string | number | boolean) => setDraft((d) => ({ ...d, [spec.key]: v }));

    return (
      <label key={spec.key} className="setting-field">
        <span className="setting-label">
          {spec.label}
          {overridden && (
            <button type="button" className="setting-reset" onClick={() => handleReset(spec.key)} disabled={busy}>
              reset
            </button>
          )}
        </span>

        {spec.kind === "bool" ? (
          <input type="checkbox" checked={Boolean(current)} onChange={(e) => set(e.target.checked)} />
        ) : spec.kind === "choice" ? (
          <select value={String(current)} onChange={(e) => set(e.target.value)}>
            {spec.choices.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : spec.key === "default_accent" ? (
          <select value={String(current)} onChange={(e) => set(e.target.value)}>
            {ACCENT_PROFILES.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            type={spec.secret ? "password" : spec.kind === "int" || spec.kind === "float" ? "number" : "text"}
            step={spec.kind === "float" ? "0.01" : undefined}
            min={spec.minimum ?? undefined}
            max={spec.maximum ?? undefined}
            value={String(current)}
            placeholder={spec.secret && payload!.secrets_set[spec.key] ? "unchanged" : ""}
            onChange={(e) => set(spec.kind === "int" || spec.kind === "float" ? e.target.value : e.target.value)}
          />
        )}

        {spec.help && <span className="setting-help">{spec.help}</span>}
      </label>
    );
  }

  return (
    <div>
      <div className="section-header">
        <h2>Settings</h2>
        <span className="meta">
          {payload.overridden.length} of {payload.specs.length} customised
        </span>
      </div>

      {status && (
        <div className="card card-pad" style={{ marginBottom: 20 }}>
          <h3 style={{ marginTop: 0 }}>System status</h3>
          <div className="status-grid">
            <StatusTile
              label="Database"
              value={status.database_reachable ? "Reachable" : "Unreachable"}
              ok={status.database_reachable}
            />
            <StatusTile
              label="Trained models"
              value={status.model_files.length ? `${status.model_files.length} file(s)` : "None"}
              detail={status.models_built_at ? `built ${relative(status.models_built_at)}` : "run a retrain"}
              ok={status.model_files.length > 0}
            />
            <StatusTile
              label="Email"
              value={status.email_configured ? "Configured" : "Off"}
              detail={status.email_configured ? undefined : "codes are copied by hand"}
              ok={status.email_configured}
              neutralWhenOff
            />
            <StatusTile label="Matches" value={status.matches.toLocaleString()} detail={`${status.leagues.length} leagues`} ok />
            <StatusTile
              label="Predictions"
              value={status.predictions.toLocaleString()}
              detail={`updated ${relative(status.latest_prediction_at)}`}
              ok={status.predictions > 0}
            />
            <StatusTile
              label="Upcoming fixtures"
              value={status.upcoming_fixtures.toLocaleString()}
              ok={status.upcoming_fixtures > 0}
            />
            <StatusTile label="Members" value={String(status.users)} detail={`${status.active_grants} with access`} ok />
            <StatusTile
              label="Unredeemed codes"
              value={String(status.unredeemed_codes)}
              ok
              neutralWhenOff
            />
          </div>
        </div>
      )}

      {GROUP_ORDER.filter((g) => payload.specs.some((s) => s.group === g)).map((group) => (
        <div className="card card-pad" style={{ marginBottom: 20 }} key={group}>
          <h3 style={{ marginTop: 0 }}>{payload.groups[group] ?? group}</h3>
          {GROUP_NOTES[group] && <p className="setting-note">{GROUP_NOTES[group]}</p>}

          <div className="settings-grid">{payload.specs.filter((s) => s.group === group).map(renderField)}</div>

          {group === "email" && (
            <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <button type="button" className="btn btn-secondary" onClick={handleTestEmail} disabled={testing || dirty}>
                {testing ? "Sending…" : "Send test email to myself"}
              </button>
              {dirty && <span className="setting-help">Save your changes first — the test uses what's saved.</span>}
              {testResult && <span className="setting-help">{testResult}</span>}
            </div>
          )}
        </div>
      ))}

      <div className="settings-actions">
        <button className="btn" onClick={handleSave} disabled={!dirty || busy}>
          {busy ? "Saving…" : dirty ? `Save ${Object.keys(draft).length} change${Object.keys(draft).length === 1 ? "" : "s"}` : "No changes"}
        </button>
        {dirty && (
          <button className="btn btn-secondary" onClick={() => setDraft({})} disabled={busy}>
            Discard
          </button>
        )}
        {saved && <span className="setting-help">{saved}</span>}
        {error && <span className="auth-error">{error}</span>}
      </div>
    </div>
  );
}

function StatusTile({
  label,
  value,
  detail,
  ok,
  neutralWhenOff,
}: {
  label: string;
  value: string;
  detail?: string;
  ok: boolean;
  neutralWhenOff?: boolean;
}) {
  const tone = ok ? "ok" : neutralWhenOff ? "neutral" : "warn";
  return (
    <div className={`status-tile status-tile-${tone}`}>
      <span className="status-tile-label">{label}</span>
      <span className="status-tile-value">{value}</span>
      {detail && <span className="status-tile-detail">{detail}</span>}
    </div>
  );
}
