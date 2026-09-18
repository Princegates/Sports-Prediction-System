import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";

function AuthLoading() {
  return (
    <div className="auth-shell">
      <div className="state-card">
        <div className="icon">◈</div>
        <div>Loading session...</div>
      </div>
    </div>
  );
}

export function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <AuthLoading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  return <Outlet />;
}

export function RequireSuperadmin() {
  const { user, loading } = useAuth();

  if (loading) return <AuthLoading />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "superadmin") return <Navigate to="/app" replace />;

  return <Outlet />;
}

/** Gates the prediction-serving pages on a live access grant -- separate
 * from RequireAuth's login check, since a logged-in account without one
 * still needs to reach /app/access to redeem a code, not be bounced to
 * /login. Mirrors the backend's require_active_access split. */
export function RequireAccess() {
  const { accessStatus, accessLoading } = useAuth();

  if (accessLoading) return <AuthLoading />;
  if (!accessStatus?.has_access) return <Navigate to="/app/access" replace />;

  return <Outlet />;
}
