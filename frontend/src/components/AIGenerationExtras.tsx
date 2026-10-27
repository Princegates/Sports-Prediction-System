import { useEffect, useState } from "react";
import { fetchPaymentOptions, startPaymentCheckout } from "../api";
import type { AIProduct } from "../types";

/** The AI Generation extras on sale (extra-run packs, a 24-hour unlimited
 * pass) as one button each, paid through Hubtel's hosted checkout. Prices and
 * which extras exist come from the server, so an admin's price change shows
 * up here with no deploy; renders nothing when none are on sale (online
 * payment off, no live access, or every price set to 0). */
export function useAIProducts(): AIProduct[] {
  const [products, setProducts] = useState<AIProduct[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchPaymentOptions()
      .then((o) => !cancelled && setProducts(o.ai_products ?? []))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return products;
}

export function AIGenerationExtras({ heading = "Need more AI Generation runs?" }: { heading?: string }) {
  const products = useAIProducts();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (products.length === 0) return null;

  async function buy(key: string) {
    setBusyKey(key);
    setError(null);
    try {
      const { checkout_url } = await startPaymentCheckout(key);
      window.location.assign(checkout_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusyKey(null);
    }
  }

  return (
    <div className="card card-pad" style={{ marginBottom: 16 }}>
      <h3 style={{ marginTop: 0, fontSize: 15 }}>{heading}</h3>
      <p className="setting-note" style={{ marginTop: 0 }}>
        Pay with mobile money or card. Extra runs are used after your daily allowance and never expire; the day pass
        removes the limit for 24 hours.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {products.map((p) => (
          <button key={p.key} className="btn ghost" disabled={busyKey !== null} onClick={() => buy(p.key)}>
            {busyKey === p.key ? "Opening checkout..." : `${p.label} -- ${p.currency}${p.amount.toLocaleString()}`}
          </button>
        ))}
      </div>
      {error && <p className="auth-error" style={{ marginBottom: 0 }}>{error}</p>}
    </div>
  );
}
