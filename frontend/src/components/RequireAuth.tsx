import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";

function AuthLoading() {
  // The session check retries across a cold-start window (the free-tier
  // backend can take 50+ seconds to wake after sitting idle -- see
  // AuthContext), so a load that's taking a few seconds is normal, not
  // stuck. Only say so once it's actually been a few seconds, so the
  // common fast case doesn't flash an explanation nobody needed.
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 4000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="auth-shell">
      <div className="state-card">
        <div className="icon">◈</div>
        <div>Loading session...</div>
        {slow && (
          <div className="meta" style={{ marginTop: 6 }}>
            Taking longer than usual -- the server may be waking up, hang tight.
          </div>
        )}
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
