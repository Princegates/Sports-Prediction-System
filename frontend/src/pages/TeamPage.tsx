import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchMatches, fetchTeam, fetchTeamForm } from "../api";
import { ErrorState } from "../components/ErrorState";
import { FormStrip } from "../components/FormStrip";
import { AiScanningState } from "../components/LoadingSkeleton";
import { MatchRow } from "../components/MatchRow";
import type { MatchSummary, Team, TeamForm } from "../types";

export function TeamPage() {
  const { id } = useParams();
  const teamId = Number(id);

  const [team, setTeam] = useState<Team | null>(null);
  const [form, setForm] = useState<TeamForm | null>(null);
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTeam(null);
    setError(null);

    Promise.all([fetchTeam(teamId), fetchTeamForm(teamId), fetchMatches({ teamId })])
      .then(([t, f, m]) => {
        if (cancelled) return;
        setTeam(t);
        setForm(f);
        setMatches(m.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()));
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [teamId]);

  if (error) return <ErrorState message={error} />;
  if (!team || !form || !matches) return <AiScanningState label="Loading team intelligence profile..." />;

  const now = Date.now();
  const played = matches.filter((m) => m.home_score !== null);
  const upcoming = matches.filter((m) => m.home_score === null && new Date(m.date).getTime() >= now).reverse().slice(0, 6);
  const recent = played.slice(0, 8);

  return (
    <div>
      <Link className="back-link" to="/">
        ← Back to dashboard
      </Link>

      <div className="match-header">
        <div>
          <h1>{team.name}</h1>
          <div className="meta">
            {team.league}
            <span>&middot;</span>
            {form.matches_played} matches on record
          </div>
        </div>
      </div>

      <div className="two-col">
        <div>
          <div className="card card-pad" style={{ marginBottom: 16 }}>
            <h3 style={{ marginBottom: 12, fontSize: 15 }}>Form</h3>
            <FormStrip results={form.recent_results} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12, marginTop: 16, fontSize: 13 }}>
              <Stat label="Points/game" value={form.points_per_game.toFixed(2)} />
              <Stat label="Goals scored/game" value={form.goals_scored_avg.toFixed(2)} />
              <Stat label="Goals conceded/game" value={form.goals_conceded_avg.toFixed(2)} />
              <Stat label="Clean sheet rate" value={`${(form.clean_sheet_rate * 100).toFixed(0)}%`} />
              <Stat label="Home goals/game" value={form.home_goals_scored_avg.toFixed(2)} />
              <Stat label="Away goals/game" value={form.away_goals_scored_avg.toFixed(2)} />
            </div>
          </div>

          <div className="card card-pad">
            <h3 style={{ marginBottom: 12, fontSize: 15 }}>Recent results</h3>
            {recent.length === 0 && <p className="badge-neutral">No finished matches in our data yet.</p>}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {recent.map((m) => (
                <MatchRow key={m.id} match={m} perspectiveTeamId={team.id} />
              ))}
            </div>
          </div>
        </div>

        <div className="card card-pad">
          <h3 style={{ marginBottom: 12, fontSize: 15 }}>Upcoming fixtures</h3>
          {upcoming.length === 0 && <p className="badge-neutral">No scheduled fixtures imported yet.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {upcoming.map((m) => (
              <MatchRow key={m.id} match={m} perspectiveTeamId={team.id} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontWeight: 700, fontSize: 16 }}>{value}</div>
    </div>
  );
}
