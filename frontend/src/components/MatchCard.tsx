import { useNavigate } from "react-router-dom";
import type { Prediction } from "../types";
import { ConfidenceTag, MostLikelyBadge } from "./MostLikelyBadge";
import { ProbabilityBar } from "./ProbabilityBar";

interface Props {
  prediction: Prediction;
  homeTeam: string;
  awayTeam: string;
  kickoff: string;
}

export function MatchCard({ prediction, homeTeam, awayTeam, kickoff }: Props) {
  const navigate = useNavigate();

  return (
    <div className="card match-card" onClick={() => navigate(`/match/${prediction.match_id}`)}>
      <div className="match-title">
        {homeTeam} vs {awayTeam}
      </div>
      <div className="match-meta">
        {new Date(kickoff).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}{" "}
        &middot; <ConfidenceTag confidence={prediction.confidence} />
      </div>

      <ProbabilityBar label="Home" probability={prediction.home_win} />
      <ProbabilityBar label="Draw" probability={prediction.draw} />
      <ProbabilityBar label="Away" probability={prediction.away_win} />

      <MostLikelyBadge outcome={prediction.global_outcome} confidence={prediction.confidence} />
    </div>
  );
}
