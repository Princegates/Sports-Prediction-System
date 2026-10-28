import { useEffect, useSyncExternalStore } from "react";
import { fetchAIGenerationQuota } from "../api";
import type { AIGenerationQuota } from "../types";
import { useAuth } from "./AuthContext";

/** One shared copy of the member's AI Generation allowance, so the page
 * header, the Generate button and the navigation all show the same number
 * and all update together after a run -- without each fetching its own. */

let current: AIGenerationQuota | null = null;
let lastFetchedAt = 0;
let inFlight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(next: AIGenerationQuota | null) {
  current = next;
  listeners.forEach((l) => l());
}

function load(): Promise<void> {
  if (!inFlight) {
    inFlight = fetchAIGenerationQuota()
      .then((q) => {
        lastFetchedAt = Date.now();
        // An incomplete answer (say, a backend mid-deploy) would otherwise
        // surface as "NaN runs left" -- show nothing instead.
        publish(typeof q?.credits === "number" && q.remaining_today !== undefined ? q : null);
      })
      .catch(() => {})
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}

/** Re-reads the allowance now -- call after a run, which changes it. */
export function refreshAIGenerationQuota(): Promise<void> {
  return load();
}

/** Runs left across the day's allowance and any bought extras; ``null``
 * when there is nothing to count down (a Super Admin, or a day pass). */
export function runsLeftOf(quota: AIGenerationQuota | null): number | null {
  if (quota === null || quota.unlimited_until || quota.remaining_today === null) return null;
  return quota.remaining_today + quota.credits;
}

export function quotaLabel(quota: AIGenerationQuota | null): string | null {
  if (!quota) return null;
  if (quota.unlimited_until) {
    return `Unlimited until ${new Date(quota.unlimited_until).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}`;
  }
  const left = runsLeftOf(quota);
  if (left === null) return "Unlimited runs";
  if (quota.remaining_today === 0 && quota.credits > 0) return `${left} extra run${left === 1 ? "" : "s"} left`;
  return `${left} run${left === 1 ? "" : "s"} left today` + (quota.credits > 0 ? ` (incl. ${quota.credits} extra)` : "");
}

export function useAIGenerationQuota(): AIGenerationQuota | null {
  const { user, accessStatus } = useAuth();
  const eligible = Boolean(user) && Boolean(accessStatus?.has_access);

  useEffect(() => {
    if (!eligible) {
      if (current !== null) publish(null);
      return;
    }
    // Several components mount together; one request serves them all.
    if (Date.now() - lastFetchedAt > 5_000) void load();
  }, [eligible, user?.id]);

  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
