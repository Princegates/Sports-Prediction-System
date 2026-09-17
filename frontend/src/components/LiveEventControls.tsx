import { useState } from "react";
import { postLiveEvent } from "../api";
import type { LivePrediction } from "../types";

const TRIGGER_OPTIONS = ["kickoff", "goal", "red_card_home", "red_card_away", "penalty", "substitution", "half_time", "match_restart"];

interface Props {
  matchId: number;
  currentMinute: number;
  currentHome: number;
  currentAway: number;
  onEvent: (live: LivePrediction) => void;
}

export function LiveEventControls({ matchId, currentMinute, currentHome, currentAway, onEvent }: Props) {
  const [minute, setMinute] = useState(currentMinute || 1);
  const [scoreHome, setScoreHome] = useState(currentHome);
  const [scoreAway, setScoreAway] = useState(currentAway);
  const [trigger, setTrigger] = useState("goal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const result = await postLiveEvent(matchId, { minute, score_home: scoreHome, score_away: scoreAway, trigger_event: trigger });
      onEvent(result);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card card-pad">
      <p style={{ marginTop: 0, fontSize: 13, color: "var(--text-secondary)" }}>
        No paid live-data feed is connected to this deployment, so there's no automatic in-play stream. Push a
        simulated event below to see the live engine recalculate the Global Most-Likely Outcome for real.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10, marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Minute
          <input
            type="number"
            min={0}
            max={120}
            value={minute}
            onChange={(e) => setMinute(Number(e.target.value))}
            style={{ display: "block", width: "100%", marginTop: 4, background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, color: "var(--text-primary)" }}
          />
        </label>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Home score
          <input
            type="number"
            min={0}
            value={scoreHome}
            onChange={(e) => setScoreHome(Number(e.target.value))}
            style={{ display: "block", width: "100%", marginTop: 4, background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, color: "var(--text-primary)" }}
          />
        </label>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Away score
          <input
            type="number"
            min={0}
            value={scoreAway}
            onChange={(e) => setScoreAway(Number(e.target.value))}
            style={{ display: "block", width: "100%", marginTop: 4, background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, color: "var(--text-primary)" }}
          />
        </label>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Event
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            style={{ display: "block", width: "100%", marginTop: 4, background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, color: "var(--text-primary)" }}
          >
            {TRIGGER_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button className="btn" onClick={submit} disabled={busy}>
        {busy ? "Recalculating..." : "Push live event"}
      </button>
      {err && <p style={{ color: "var(--critical)", fontSize: 12.5, marginTop: 8 }}>{err}</p>}
    </div>
  );
}
