import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RequireAccess, RequireAuth, RequireSuperadmin } from "./components/RequireAuth";
import { Access } from "./pages/Access";
import { AccountStatus } from "./pages/AccountStatus";
import { AdminUsers } from "./pages/AdminUsers";
import { BetCodes } from "./pages/BetCodes";
import { Dashboard } from "./pages/Dashboard";
import { HowItWorks } from "./pages/HowItWorks";
import { Live } from "./pages/Live";
import { Login } from "./pages/Login";
import { Markets } from "./pages/Markets";
import { MatchDetail } from "./pages/MatchDetail";
import { Predictions } from "./pages/Predictions";
import { Profile } from "./pages/Profile";
import { Register } from "./pages/Register";
import { Responsible } from "./pages/Responsible";
import { Settings } from "./pages/Settings";
import { TeamPage } from "./pages/TeamPage";
import { Welcome } from "./pages/Welcome";

/**
 * Route layout is split in two:
 *
 * - `/`, `/how-it-works`, `/responsible` and the auth screens are public. An
 *   anonymous visitor gets a real website rather than being bounced to a
 *   login form with no explanation of what they'd be logging into.
 * - `/app/*` is the product, behind RequireAuth. Logging in only needs an
 *   account that isn't suspended -- reaching the prediction-serving pages
 *   (match detail, the full predictions table, markets, live, teams)
 *   additionally needs a live access grant, checked separately by
 *   RequireAccess. The dashboard is the one exception: it's free for any
 *   logged-in account, since it's how a no-code account sees the free-tier
 *   headline picks and the redeem-a-code prompt, rather than being bounced
 *   straight to /app/access with nothing to look at first.
 *
 * The signed-in app lives under its own prefix rather than sharing `/` with
 * the marketing page so neither has to know about the other's state.
 */
export default function App() {
  return (
    <Routes>
      {/* ---- Public site ---- */}
      <Route path="/" element={<Welcome />} />
      <Route path="/how-it-works" element={<HowItWorks />} />
      <Route path="/responsible" element={<Responsible />} />
      <Route path="/account-status" element={<AccountStatus />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      {/* ---- The product ---- */}
      <Route element={<RequireAuth />}>
        <Route path="/app" element={<AppShell />}>
          <Route path="profile" element={<Profile />} />
          <Route path="access" element={<Access />} />
          <Route index element={<Dashboard />} />

          <Route element={<RequireAccess />}>
            <Route path="match/:id" element={<MatchDetail />} />
            <Route path="predictions" element={<Predictions />} />
            <Route path="markets" element={<Markets />} />
            <Route path="betcodes" element={<BetCodes />} />
            <Route path="live" element={<Live />} />
            <Route path="teams/:id" element={<TeamPage />} />
          </Route>

          <Route element={<RequireSuperadmin />}>
            <Route path="admin" element={<AdminUsers />} />
            <Route path="admin/settings" element={<Settings />} />
          </Route>
        </Route>
      </Route>

      {/* Old bookmarks from before the /app prefix existed, plus anything
          unrecognized, land on the welcome page rather than a blank screen. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
