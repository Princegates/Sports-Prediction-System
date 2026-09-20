import { useMemo, useState } from "react";
import type { MatchSummary } from "../types";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function dateKeyFromIso(iso: string): string {
  return dateKey(new Date(iso));
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

/**
 * A month grid of fixture counts, built from every SCHEDULED match this
 * league has -- no days-ahead cap, unlike the coupon's own market fetch --
 * so someone planning ahead can see a whole season's worth of match days
 * at a glance and jump straight to one, instead of only ever browsing a
 * rolling 3/7/14-day window.
 */
export function FixtureCalendar({
  matches, selectedDate, onSelectDate,
}: {
  matches: MatchSummary[];
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
}) {
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState(() =>
    startOfMonth(selectedDate ? new Date(`${selectedDate}T00:00:00`) : today),
  );

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const m of matches) {
      const key = dateKeyFromIso(m.date);
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [matches]);

  const cells = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const out: (Date | null)[] = [];
    for (let i = 0; i < first.getDay(); i++) out.push(null);
    for (let d = 1; d <= daysInMonth; d++) out.push(new Date(year, month, d));
    return out;
  }, [cursor]);

  const canGoBack = startOfMonth(cursor).getTime() > startOfMonth(today).getTime();
  const todayKey = dateKey(today);

  return (
    <div className="fixture-calendar">
      <div className="fixture-calendar-header">
        <button
          type="button"
          className="btn ghost"
          disabled={!canGoBack}
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
          aria-label="Previous month"
        >
          ‹
        </button>
        <strong>{cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
        <button
          type="button"
          className="btn ghost"
          onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
          aria-label="Next month"
        >
          ›
        </button>
      </div>

      <div className="fixture-calendar-weekdays">
        {WEEKDAYS.map((w) => (
          <span key={w}>{w}</span>
        ))}
      </div>

      <div className="fixture-calendar-grid">
        {cells.map((d, i) => {
          if (!d) return <span key={`blank-${i}`} className="fixture-calendar-cell empty" />;
          const key = dateKey(d);
          const count = counts.get(key) ?? 0;
          const isPast = key < todayKey;
          return (
            <button
              type="button"
              key={key}
              className={[
                "fixture-calendar-cell",
                key === selectedDate && "selected",
                key === todayKey && "today",
                count === 0 && "no-fixtures",
              ]
                .filter(Boolean)
                .join(" ")}
              disabled={isPast}
              onClick={() => onSelectDate(key === selectedDate ? null : key)}
              title={count > 0 ? `${count} fixture${count === 1 ? "" : "s"}` : "No scheduled fixtures"}
            >
              <span className="day-number">{d.getDate()}</span>
              {count > 0 && <span className="fixture-dot" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
