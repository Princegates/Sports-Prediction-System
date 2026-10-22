import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RequireAccess, RequireAuth, RequireSuperadmin } from "./components/RequireAuth";

// Lazy per route rather than eagerly imported: these 22 page files are what
// made the production bundle 500+ KB of JS -- without this, a first-time
// mobile visitor to the public landing page downloads the admin panel,
// the AI-generation wizard and every other page before anything renders.
// Route-level splitting means a visitor only ever fetches the page(s) they
// actually navigate to.
const Access = lazy(() => import("./pages/Access").then((m) => ({ default: m.Access })));
const AccountStatus = lazy(() => import("./pages/AccountStatus").then((m) => ({ default: m.AccountStatus })));
const ActivityLog = lazy(() => import("./pages/ActivityLog").then((m) => ({ default: m.ActivityLog })));
const AdminUsers = lazy(() => import("./pages/AdminUsers").then((m) => ({ default: m.AdminUsers })));
const MyWinningSlips = lazy(() => import("./pages/MyWinningSlips").then((m) => ({ default: m.MyWinningSlips })));
const AdminWinningSlips = lazy(() => import("./pages/AdminWinningSlips").then((m) => ({ default: m.AdminWinningSlips })));
const AIPicks = lazy(() => import("./pages/AIPicks").then((m) => ({ default: m.AIPicks })));
const BetCodes = lazy(() => import("./pages/BetCodes").then((m) => ({ default: m.BetCodes })));
const CookiePolicy = lazy(() => import("./pages/CookiePolicy").then((m) => ({ default: m.CookiePolicy })));
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword").then((m) => ({ default: m.ForgotPassword })));
const FixturePreview = lazy(() => import("./pages/FixturePreview").then((m) => ({ default: m.FixturePreview })));
const Fixtures = lazy(() => import("./pages/Fixtures").then((m) => ({ default: m.Fixtures })));
const HowItWorks = lazy(() => import("./pages/HowItWorks").then((m) => ({ default: m.HowItWorks })));
const LeaguePredictions = lazy(() => import("./pages/LeaguePredictions").then((m) => ({ default: m.LeaguePredictions })));
const Live = lazy(() => import("./pages/Live").then((m) => ({ default: m.Live })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));
const Markets = lazy(() => import("./pages/Markets").then((m) => ({ default: m.Markets })));
const MatchDetail = lazy(() => import("./pages/MatchDetail").then((m) => ({ default: m.MatchDetail })));
const Predictions = lazy(() => import("./pages/Predictions").then((m) => ({ default: m.Predictions })));
const Pricing = lazy(() => import("./pages/Pricing").then((m) => ({ default: m.Pricing })));
const Profile = lazy(() => import("./pages/Profile").then((m) => ({ default: m.Profile })));
const Register = lazy(() => import("./pages/Register").then((m) => ({ default: m.Register })));
const ResetPassword = lazy(() => import("./pages/ResetPassword").then((m) => ({ default: m.ResetPassword })));
const Responsible = lazy(() => import("./pages/Responsible").then((m) => ({ default: m.Responsible })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));
const TeamPage = lazy(() => import("./pages/TeamPage").then((m) => ({ default: m.TeamPage })));
const TryAIGeneration = lazy(() => import("./pages/TryAIGeneration").then((m) => ({ default: m.TryAIGeneration })));
const VerifyEmail = lazy(() => import("./pages/VerifyEmail").then((m) => ({ default: m.VerifyEmail })));
const Welcome = lazy(() => import("./pages/Welcome").then((m) => ({ default: m.Welcome })));

function RouteFallback() {
  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "40vh" }}>
      <span className="route-spinner" role="status" aria-label="Loading" />
    </div>
  );
}

/**
 * Route layout is split in two:
 *
 * - `/`, `/how-it-works`, `/pricing`, `/responsible`, `/cookie-policy`, `/fixtures`,
 *   `/predict/:slugId` and the auth screens are public. An anonymous visitor gets a real website
 *   rather than being bounced to a login form with no explanation of what
 *   they'd be logging into -- `/fixtures` and the per-match `/predict/*`
 *   pages exist specifically so a search engine has real, indexable content
 *   to send someone to (see public/robots.txt and sitemap.xml).
 * - `/app/*` is the product, behind RequireAuth. Logging in only needs an
 *   account that isn't suspended -- reaching the prediction-serving pages
 *   (match detail, the full predictions table, markets, live, teams)
 *   additionally needs a live access grant, checked separately by
 *   RequireAccess. The dashboard and AI Picks are the exceptions: both free
 *   for any logged-in account, since together they're how a no-code account
 *   sees the free-tier headline picks, every featured slip, and the
 *   redeem-a-code prompt, rather than being bounced straight to /app/access
 *   with nothing to look at first.
 *
 * The signed-in app lives under its own prefix rather than sharing `/` with
 * the marketing page so neither has to know about the other's state.
 */
export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        {/* ---- Public site ---- */}
        <Route path="/" element={<Welcome />} />
        <Route path="/how-it-works" element={<HowItWorks />} />
        <Route path="/pricing" element={<Pricing />} />
        <Route path="/fixtures" element={<Fixtures />} />
        <Route path="/try-ai-generation" element={<TryAIGeneration />} />
        <Route path="/predict/:slugId" element={<FixturePreview />} />
        <Route path="/predictions/:leagueSlug" element={<LeaguePredictions />} />
        <Route path="/responsible" element={<Responsible />} />
        <Route path="/cookie-policy" element={<CookiePolicy />} />
        <Route path="/account-status" element={<AccountStatus />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/verify-email" element={<VerifyEmail />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />

        {/* ---- The product ---- */}
        <Route element={<RequireAuth />}>
          <Route path="/app" element={<AppShell />}>
            <Route path="profile" element={<Profile />} />
            <Route path="access" element={<Access />} />
            <Route path="ai-picks" element={<AIPicks />} />
            <Route path="share-win" element={<MyWinningSlips />} />
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
              <Route path="admin/activity-log" element={<ActivityLog />} />
              <Route path="admin/settings" element={<Settings />} />
              <Route path="admin/winning-slips" element={<AdminWinningSlips />} />
            </Route>
          </Route>
        </Route>

        {/* Old bookmarks from before the /app prefix existed, plus anything
            unrecognized, land on the welcome page rather than a blank screen. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
