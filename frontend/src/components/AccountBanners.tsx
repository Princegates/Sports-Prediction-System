import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchVerificationStatus, resendVerification } from "../api";
import { useAuth } from "../lib/AuthContext";
import type { VerificationStatus } from "../types";

const ENDING_SOON_HOURS = 48;

/** Account-level notices shown above every app page: a pending email
 * confirmation (which is what starts the free trial), access that ends
 * within two days, and a running self-set break. Renders nothing in the
 * common case. */
export function AccountBanners() {
  const { user, accessStatus } = useAuth();
  const [verification, setVerification] = useState<VerificationStatus | null>(null);
  const [resend, setResend] = useState<"idle" | "sending" | "sent" | string>("idle");

  useEffect(() => {
    if (!user || user.email_verified) {
      setVerification(null);
      return;
    }
    fetchVerificationStatus().then(setVerification).catch(() => setVerification(null));
  }, [user]);

  async function handleResend() {
    setResend("sending");
    try {
      await resendVerification();
      setResend("sent");
    } catch (err) {
      setResend(err instanceof Error ? err.message : String(err));
    }
  }

  const expiresAt = accessStatus?.has_access && accessStatus.expires_at ? new Date(accessStatus.expires_at) : null;
  const hoursLeft = expiresAt ? (expiresAt.getTime() - Date.now()) / 3_600_000 : null;
  const endingSoon = user?.role !== "superadmin" && hoursLeft != null && hoursLeft > 0 && hoursLeft <= ENDING_SOON_HOURS;

  const pausedUntil = user?.paused_until ? new Date(user.paused_until) : null;
  const paused = pausedUntil != null && pausedUntil.getTime() > Date.now();

  return (
    <>
      {verification?.pending && (
        <div className="account-banner" role="status">
          <span>
            <strong>Confirm your email to start your free trial.</strong>{" "}
            {verification.trial_days > 0 && <>You get {verification.trial_days} days of full access. </>}
            We sent a link to {user?.email}.
          </span>
          {resend === "sent" ? (
            <span className="account-banner-note">Sent -- check your inbox and spam folder.</span>
          ) : (
            <button type="button" className="btn ghost btn-sm" onClick={handleResend} disabled={resend === "sending"}>
              {resend === "sending" ? "Sending..." : "Resend link"}
            </button>
          )}
          {resend !== "idle" && resend !== "sending" && resend !== "sent" && <span className="account-banner-note">{resend}</span>}
        </div>
      )}
      {endingSoon && (
        <div className="account-banner" role="status">
          <span>
            <strong>
              Your access ends in{" "}
              {hoursLeft < 1 ? "under an hour" : `about ${Math.round(hoursLeft)} hour${Math.round(hoursLeft) === 1 ? "" : "s"}`}.
            </strong>{" "}
            After that you'll be on the free tier -- your history and saved slips stay put.
          </span>
          <Link className="btn btn-sm" to="/app/access">
            Keep full access
          </Link>
        </div>
      )}
      {paused && (
        <div className="account-banner" role="status">
          <span>
            <strong>You're taking a break.</strong> Predictions come back on{" "}
            {pausedUntil.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.
          </span>
        </div>
      )}
    </>
  );
}
