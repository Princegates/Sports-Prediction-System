import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RequireAuth, RequireSuperadmin } from "./components/RequireAuth";
import { Dashboard } from "./pages/Dashboard";
import { Live } from "./pages/Live";
import { Login } from "./pages/Login";
import { MatchDetail } from "./pages/MatchDetail";
import { Predictions } from "./pages/Predictions";
import { Register } from "./pages/Register";
import { AdminUsers } from "./pages/AdminUsers";
import { Profile } from "./pages/Profile";
import { TeamPage } from "./pages/TeamPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/match/:id" element={<MatchDetail />} />
          <Route path="/predictions" element={<Predictions />} />
          <Route path="/live" element={<Live />} />
          <Route path="/teams/:id" element={<TeamPage />} />
          <Route path="/profile" element={<Profile />} />

          <Route element={<RequireSuperadmin />}>
            <Route path="/admin" element={<AdminUsers />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
