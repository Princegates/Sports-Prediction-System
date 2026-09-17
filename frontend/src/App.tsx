import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { MatchDetail } from "./pages/MatchDetail";

const LEAGUES = [
  "English Premier League",
  "English Championship",
  "Spanish La Liga",
  "German Bundesliga",
  "Italian Serie A",
  "French Ligue 1",
];

export default function App() {
  const [league, setLeague] = useState(LEAGUES[0]);

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <h1>AI Match Intelligence</h1>
          <div className="subtitle">Global Most-Likely Outcome across every supported market</div>
        </div>
        <select className="league-select" value={league} onChange={(e) => setLeague(e.target.value)}>
          {LEAGUES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </header>

      <Routes>
        <Route path="/" element={<Dashboard league={league} />} />
        <Route path="/match/:id" element={<MatchDetail />} />
      </Routes>
    </div>
  );
}
