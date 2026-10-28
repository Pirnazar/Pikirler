const pad = (n: number) => String(n).padStart(2, "0");

export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "now", "5m", "3h", "2d", then "Oct 3" / "Oct 3, 2024". */
export function rel(ts: string): string {
  const d = new Date(ts), n = new Date(), diff = (n.getTime() - d.getTime()) / 1000;
  if (diff < 45) return "now";
  if (diff < 3600) return Math.round(diff / 60) + "m";
  if (diff < 86400) return Math.round(diff / 3600) + "h";
  if (diff < 86400 * 6) return Math.round(diff / 86400) + "d";
  const o: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (d.getFullYear() !== n.getFullYear()) o.year = "numeric";
  return d.toLocaleDateString("en-US", o);
}

export function full(ts: string): string {
  const d = new Date(ts);
  return (
    d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" }) +
    " · " + pad(d.getHours()) + ":" + pad(d.getMinutes())
  );
}

export function short(ts: string): string {
  return new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function mmss(s: number): string {
  s = Math.max(0, Math.floor(s));
  return Math.floor(s / 60) + ":" + pad(s % 60);
}

export function fileSize(n: number): string {
  return n < 1048576 ? Math.max(1, Math.round(n / 1024)) + " KB" : (n / 1048576).toFixed(1) + " MB";
}

export function plural(n: number, one: string, many = one + "s"): string {
  return n + " " + (n === 1 ? one : many);
}

/** Hashtags as the backend extracts them: letters, digits, _ and -. */
export function parseTags(text: string): string[] {
  return [...new Set([...(text || "").matchAll(/#([\p{L}\p{N}_-]+)/gu)].map((m) => m[1]))];
}

export function snip(text: string, n = 90): string {
  const p = (text || "").replace(/\s+/g, " ").trim();
  return p.length > n ? p.slice(0, n).trimEnd() + "…" : p;
}

const URL_RE = /\bhttps?:\/\/[^\s<>"')]+/i;

export function firstLink(text: string): { url: string; domain: string; title: string } | null {
  const m = (text || "").match(URL_RE);
  if (!m) return null;
  try {
    const u = new URL(m[0]);
    const path = decodeURIComponent(u.pathname).replace(/\/$/, "").split("/").pop() || "";
    return { url: u.href, domain: u.hostname.replace(/^www\./, ""), title: path.replace(/[-_]+/g, " ") || u.hostname };
  } catch {
    return null;
  }
}

export const LANGS: Record<string, string> = { tk: "Türkmen", ru: "Русский", en: "English", zh: "中文" };
