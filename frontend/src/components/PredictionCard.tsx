import { useNavigate } from "react-router-dom";
import type { Prediction } from "../types";
import { ConfidenceTag, MostLikelyOutcome } from "./MostLikelyOutcome";
import { ProbabilityBar } from "./ProbabilityBar";

interface Props {
  prediction: Prediction;
  homeTeam: string;
  awayTeam: string;
  kickoff: string;
  competition: string;
  isLive?: boolean;
  liveScore?: { home: number; away: number; minute: number };
}

export function PredictionCard({ prediction, homeTeam, awayTeam, kickoff, competition, isLive, liveScore }: Props) {
  const navigate = useNavigate();

  return (
    <div className="card match-card" onClick={() => navigate(`/match/${prediction.match_id}`)}>
      <div className="match-card-top">
        <span className="match-competition">{competition}</span>
        {isLive ? (
          <span className="live-badge">
            <span className="live-dot" /> LIVE {liveScore ? `${liveScore.minute}'` : ""}
          </span>
        ) : (
          <ConfidenceTag confidence={prediction.confidence} />
        )}
      </div>

      <div className="match-teams">
        <span className="team-name">{homeTeam}</span>
        {liveScore ? (
          <span className="vs-divider tabular-nums" style={{ fontSize: 15, fontWeight: 800, color: "var(--text-primary)" }}>
            {liveScore.home} - {liveScore.away}
          </span>
        ) : (
          <span className="vs-divider">vs</span>
        )}
        <span className="team-name away">{awayTeam}</span>
      </div>

      <div className="match-meta-row">
        {new Date(kickoff).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}
      </div>

      <div>
        <ProbabilityBar label="Home" probability={prediction.home_win} variant="home" />
        <ProbabilityBar label="Draw" probability={prediction.draw} variant="draw" />
        <ProbabilityBar label="Away" probability={prediction.away_win} variant="away" />
      </div>

      <MostLikelyOutcome outcome={prediction.global_outcome} confidence={prediction.confidence} />
    </div>
  );
}
