import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTeams } from "../api";
import type { Team } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
  leagues: string[];
}

export function SearchCommand({ open, onClose, leagues }: Props) {
  const [query, setQuery] = useState("");
  const [teams, setTeams] = useState<Team[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    setTimeout(() => inputRef.current?.focus(), 10);

    if (teams.length === 0) {
      Promise.all(leagues.map((l) => fetchTeams(l).catch(() => [])))
        .then((results) => setTeams(results.flat()))
        .catch(() => setTeams([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const results = useMemo(() => {
    if (!query.trim()) return teams.slice(0, 8);
    const q = query.toLowerCase();
    return teams.filter((t) => t.name.toLowerCase().includes(q)).slice(0, 10);
  }, [query, teams]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  function select(team: Team) {
    onClose();
    navigate(`/teams/${team.id}`);
  }

  if (!open) return null;

  return (
    <div className="command-backdrop" onClick={onClose}>
      <div className="command-modal" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="command-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search teams..."
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActiveIndex((i) => Math.min(i + 1, results.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActiveIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter" && results[activeIndex]) {
              select(results[activeIndex]);
            }
          }}
        />
        <div className="command-results">
          {results.length === 0 && <div className="command-empty">No teams found. Try a different spelling.</div>}
          {results.map((team, i) => (
            <div
              key={team.id}
              className={`command-item${i === activeIndex ? " active" : ""}`}
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => select(team)}
            >
              <span>{team.name}</span>
              <span className="league">{team.league}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
