import type { MatchSummary, Prediction } from "../types";

/** Wraps a CSV field in quotes only when it needs them, doubling any quotes inside. */
function csvField(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function toCsv(rows: { match: MatchSummary; prediction: Prediction }[]): string {
  const header = ["Kickoff", "League", "Home Team", "Away Team", "AI Pick", "Probability %", "Confidence", "Status"];
  const lines = rows.map(({ match, prediction }) =>
    [
      new Date(match.date).toLocaleString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
      match.league,
      match.home_team.name,
      match.away_team.name,
      prediction.global_outcome.selection,
      (prediction.global_outcome.probability * 100).toFixed(0),
      prediction.confidence,
      match.status,
    ]
      .map(csvField)
      .join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

/** Triggers a browser download of the given text as a file -- no server round trip. */
export function downloadCsv(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
