import { useNavigate } from "react-router-dom";
import type { MatchSummary } from "../types";

export function MatchRow({ match, perspectiveTeamId }: { match: MatchSummary; perspectiveTeamId?: number }) {
  const navigate = useNavigate();
  const isHome = perspectiveTeamId ? match.home_team.id === perspectiveTeamId : true;
  const opponent = isHome ? match.away_team.name : match.home_team.name;
  const played = match.home_score !== null && match.away_score !== null;

  let resultTag: string | null = null;
  let resultClass = "";
  let ownScore: number | null = null;
  let oppScore: number | null = null;
  if (played && perspectiveTeamId) {
    ownScore = isHome ? match.home_score! : match.away_score!;
    oppScore = isHome ? match.away_score! : match.home_score!;
    if (ownScore > oppScore) {
      resultTag = "W";
      resultClass = "W";
    } else if (ownScore === oppScore) {
      resultTag = "D";
      resultClass = "D";
    } else {
      resultTag = "L";
      resultClass = "L";
    }
  }

  // Score is always shown from the perspective team's own point of view
  // (own score first), not raw home-away order -- "0-1" for an away win
  // reads as a loss at a glance otherwise.
  const scoreText = played
    ? perspectiveTeamId
      ? `${ownScore}-${oppScore}`
      : `${match.home_score}-${match.away_score}`
    : new Date(match.date).toLocaleDateString(undefined, { month: "short", day: "numeric" });

  return (
    <div
      className="transparency-row"
      style={{ cursor: "pointer" }}
      onClick={() => navigate(`/match/${match.id}`)}
    >
      {resultTag && <span className={`form-chip ${resultClass}`}>{resultTag}</span>}
      <span className="name" style={{ width: "auto", flex: 1 }}>
        {isHome ? "vs" : "@"} {opponent}
      </span>
      <span className="pct" style={{ width: "auto" }}>
        {scoreText}
      </span>
    </div>
  );
}
