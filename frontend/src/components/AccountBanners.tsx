import { useEffect, useState } from "react";
import { fetchVerificationStatus, resendVerification } from "../api";
import { useAuth } from "../lib/AuthContext";
import type { VerificationStatus } from "../types";

/** Account-level notices shown above every app page: a pending email
 * confirmation (which is what starts the free trial) and a running
 * self-set break. Renders nothing in the common case. */
export function AccountBanners() {
  const { user } = useAuth();
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
