/** Mise en forme (français). */
export const fmtDateTime = (ts?: number | null) =>
  ts ? new Date(ts * 1000).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
export const fmtBytes = (n?: number | null) =>
  n == null ? "—" : n >= 1e12 ? `${(n / 1e12).toFixed(2)} To` : n >= 1e9 ? `${(n / 1e9).toFixed(1)} Go` : n >= 1e6 ? `${(n / 1e6).toFixed(0)} Mo` : `${(n / 1e3).toFixed(0)} ko`;
export function fmtDuration(s: number): string {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} j ${Math.round((s % 86400) / 3600)} h`;
}
export const ago = (ts?: number | null) => (ts ? fmtDuration(Math.max(0, Date.now() / 1000 - ts)) : "—");
export const ROLE_LABEL: Record<string, string> = { integrator: "Intégrateur", pharmacist: "Pharmacien" };
