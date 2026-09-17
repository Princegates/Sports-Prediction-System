import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Dashboard } from "./pages/Dashboard";
import { Live } from "./pages/Live";
import { MatchDetail } from "./pages/MatchDetail";
import { Predictions } from "./pages/Predictions";
import { TeamPage } from "./pages/TeamPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/match/:id" element={<MatchDetail />} />
        <Route path="/predictions" element={<Predictions />} />
        <Route path="/live" element={<Live />} />
        <Route path="/teams/:id" element={<TeamPage />} />
      </Route>
    </Routes>
  );
}
