import { useEffect, useState } from "react";
import { fetchBranding } from "../api";
import { useAuth } from "../lib/AuthContext";
import { useAIGenerationQuota } from "../lib/aiQuota";
import { whatsappLink } from "../lib/whatsapp";
import { AIGenerationExtras, useAIProducts } from "./AIGenerationExtras";

function formatFreeAt(iso: string): string {
  const at = new Date(iso);
  const sameDay = at.toDateString() === new Date().toDateString();
  const time = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return sameDay ? `today at ${time}` : `${at.toLocaleDateString([], { weekday: "short" })} at ${time}`;
}

/** What a member sees the moment they're out of AI Generation runs: when the
 * next one frees up, the extras they can buy (when online payment is on), and
 * -- always, as the backup -- a WhatsApp message to an admin, which is also
 * the only way to get more when online payment isn't switched on. Meant to
 * sit right where the disabled Generate button is, so there's always a next
 * step on the same screen. */
export function OutOfRunsPanel({ heading = "You've used all your AI Generation runs for today" }: { heading?: string }) {
  const { user } = useAuth();
  const quota = useAIGenerationQuota();
  const [whatsapp, setWhatsapp] = useState<string | null>(null);
  const canBuy = useAIProducts().length > 0;
  const comesBack = "runs come back on a rolling 24-hour basis";

  useEffect(() => {
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, []);

  return (
    <div style={{ marginTop: 16 }} role="status">
      <p style={{ margin: "0 0 4px", fontWeight: 600 }}>{heading}</p>
      <p className="setting-note" style={{ margin: "0 0 12px" }}>
        {quota?.next_free_at
          ? `Your next run frees up ${formatFreeAt(quota.next_free_at)} -- ${comesBack}.`
          : `${comesBack[0].toUpperCase()}${comesBack.slice(1)}.`}
        {canBuy ? " Or get more now:" : " Need more sooner? An admin can add runs for you."}
      </p>

      <AIGenerationExtras heading="Buy more runs" />

      {whatsapp && (
        <p className="setting-note" style={{ margin: 0 }}>
          {canBuy ? "Prefer another way to pay, or had a problem paying?" : "Ask for more runs:"}{" "}
          <a
            href={whatsappLink(
              whatsapp,
              `Hi, I've used all my AI Generation runs and would like more. My account email is ${user?.email ?? ""}.`,
            )}
            target="_blank"
            rel="noreferrer noopener"
          >
            Message an admin on WhatsApp
          </a>
          .
        </p>
      )}
    </div>
  );
}
