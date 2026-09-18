import { useMemo } from "react";

/** Local YYYY-MM-DD. Deliberately not toISOString(), which converts to UTC
 *  and lands on the wrong day for anyone east or west of it. */
export function localDayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

interface DateStripProps {
  /** Kickoff times of everything currently loaded, used for the per-day counts. */
  dates: Date[];
  /** How many days forward to show, starting today. */
  horizonDays: number;
  /** Selected day key, or null for "all upcoming". */
  value: string | null;
  onChange: (dayKey: string | null) => void;
}

/**
 * A row of upcoming days, each showing how many matches fall on it.
 *
 * A plain date picker would be less work and worse: on a fixtures page the
 * question is "which days have matches", and a picker makes you open it and
 * guess, one day at a time. The counts turn that into a glance.
 *
 * Days with nothing scheduled are shown but not selectable -- seeing that
 * Tuesday is empty is useful; being able to select an empty Tuesday is not.
 */
export function DateStrip({ dates, horizonDays, value, onChange }: DateStripProps) {
  const days = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of dates) {
      const key = localDayKey(d);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return Array.from({ length: horizonDays }, (_, i) => {
      const date = new Date(today);
      date.setDate(today.getDate() + i);
      const key = localDayKey(date);
      return { key, date, count: counts.get(key) ?? 0, isToday: i === 0 };
    });
  }, [dates, horizonDays]);

  return (
    <div className="date-strip" role="group" aria-label="Filter by match day">
      <button
        type="button"
        className={`date-cell date-cell-all${value === null ? " active" : ""}`}
        onClick={() => onChange(null)}
        aria-pressed={value === null}
      >
        <span className="date-cell-weekday">All</span>
        <span className="date-cell-day">{dates.length}</span>
        <span className="date-cell-count">matches</span>
      </button>

      {days.map(({ key, date, count, isToday }) => {
        const selected = value === key;
        return (
          <button
            key={key}
            type="button"
            className={`date-cell${selected ? " active" : ""}${count === 0 ? " empty" : ""}${isToday ? " today" : ""}`}
            onClick={() => count > 0 && onChange(selected ? null : key)}
            disabled={count === 0}
            aria-pressed={selected}
            aria-label={`${date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}, ${count} ${count === 1 ? "match" : "matches"}`}
          >
            <span className="date-cell-weekday">
              {isToday ? "Today" : date.toLocaleDateString(undefined, { weekday: "short" })}
            </span>
            <span className="date-cell-day">{date.getDate()}</span>
            <span className="date-cell-count">{count === 0 ? "—" : count}</span>
          </button>
        );
      })}
    </div>
  );
}
