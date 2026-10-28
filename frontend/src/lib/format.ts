import { getLang, getLocale, translate } from "../i18n";

const pad = (n: number) => String(n).padStart(2, "0");

export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "now", "5m", "3h", "2d", then "Oct 3" / "Oct 3, 2024". */
export function rel(ts: string): string {
  const d = new Date(ts), n = new Date(), diff = (n.getTime() - d.getTime()) / 1000;
  if (diff < 45) return translate("r_now");
  if (diff < 3600) return translate("r_m", { n: Math.round(diff / 60) });
  if (diff < 86400) return translate("r_h", { n: Math.round(diff / 3600) });
  if (diff < 86400 * 6) return translate("r_d", { n: Math.round(diff / 86400) });
  const o: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (d.getFullYear() !== n.getFullYear()) o.year = "numeric";
  return fmtDate(d, o);
}

export function full(ts: string): string {
  const d = new Date(ts);
  return (
    fmtDate(d, { weekday: "short", month: "short", day: "numeric", year: "numeric" }) +
    " · " + pad(d.getHours()) + ":" + pad(d.getMinutes())
  );
}

export function short(ts: string): string {
  return fmtDate(new Date(ts), { month: "short", day: "numeric", year: "numeric" });
}

export function mmss(s: number): string {
  s = Math.max(0, Math.floor(s));
  return Math.floor(s / 60) + ":" + pad(s % 60);
}

// Many browsers ship without Turkmen date data and silently fall back to
// another language, so Turkmen names are built in.
const TK_INTL = Intl.DateTimeFormat.supportedLocalesOf(["tk"]).length > 0;
const TK_MONTHS = ["ýanwar", "fewral", "mart", "aprel", "maý", "iýun", "iýul", "awgust", "sentýabr", "oktýabr", "noýabr", "dekabr"];
const TK_MONTHS_SHORT = ["ýan", "few", "mar", "apr", "maý", "iýn", "iýl", "awg", "sen", "okt", "noý", "dek"];
const TK_DAYS = ["Ýekşenbe", "Duşenbe", "Sişenbe", "Çarşenbe", "Penşenbe", "Anna", "Şenbe"];
const TK_DAYS_SHORT = ["Ýek", "Duş", "Siş", "Çar", "Pen", "Ann", "Şen"];
const cap = (s: string) => s[0].toLocaleUpperCase("tr") + s.slice(1);

function tkDate(d: Date, o: Intl.DateTimeFormatOptions): string {
  const m = o.month === "narrow" ? cap(TK_MONTHS[d.getMonth()])[0]
    : o.month === "short" ? TK_MONTHS_SHORT[d.getMonth()]
      : o.month ? TK_MONTHS[d.getMonth()] : "";
  const parts: string[] = [];
  if (o.day) parts.push(String(d.getDate()));
  if (m) parts.push(o.day ? m : cap(m));
  if (o.year) parts.push(String(d.getFullYear()));
  const date = parts.join(" ");
  if (!o.weekday) return date;
  const wd = (o.weekday === "long" ? TK_DAYS : TK_DAYS_SHORT)[d.getDay()];
  return date ? `${wd}, ${date}` : wd;
}

/** Date formatting in the interface language. */
export function fmtDate(d: Date, o: Intl.DateTimeFormatOptions): string {
  if (getLang() === "tk" && !TK_INTL) return tkDate(d, o);
  return d.toLocaleDateString(getLocale(), o);
}

export function fileSize(n: number): string {
  return n < 1048576 ? Math.max(1, Math.round(n / 1024)) + " KB" : (n / 1048576).toFixed(1) + " MB";
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
