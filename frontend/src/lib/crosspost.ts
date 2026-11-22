import { useEffect, useState } from "react";
import { del, get, put, type Thought } from "../api/client";
import type { components } from "../api/schema";

export type SiteAccount = components["schemas"]["SiteAccountOut"];
export type Site = components["schemas"]["CrosspostTarget"];
export type Crosspost = components["schemas"]["CrosspostOut"];

/** X allows 280 characters; a longer thought is cut with an ellipsis. */
export const X_LIMIT = 280;

export function xText(content: string): { text: string; cut: boolean } {
  const chars = Array.from(content.trim());
  if (chars.length <= X_LIMIT) return { text: chars.join(""), cut: false };
  return { text: chars.slice(0, X_LIMIT - 1).join("").trimEnd() + "…", cut: true };
}

/** X's free web intent: opens X with the text filled in; the owner presses Post. */
export function xIntentUrl(content: string): string {
  return "https://x.com/intent/post?text=" + encodeURIComponent(xText(content).text);
}

/** Opens a blank tab now, while the click still counts as a user action, so
 *  the browser does not block it; the X address is filled in once publishing
 *  succeeds. Returns null when the browser blocked it anyway. */
export function reserveTab(): Window | null {
  const w = window.open("about:blank", "_blank");
  if (w) w.opener = null;
  return w;
}

export function openX(content: string, tab?: Window | null): boolean {
  const url = xIntentUrl(content);
  if (tab && !tab.closed) {
    tab.location.href = url;
    return true;
  }
  return !!window.open(url, "_blank", "noopener");
}

// --- accounts on other sites: one shared, lazily loaded copy for the whole app ---

/** Sites a thought can be published on from Pikirler (X goes through its web intent). */
export const SITES: readonly Site[] = ["teswirler", "makalam"];

export function siteName(site: string): string {
  return site === "teswirler" ? "Teswirler" : site === "makalam" ? "makalam" : site;
}

/** makalam: 500 characters a post; a longer thought goes as a chain of up to 25. */
export const MAKALAM_LIMIT = 500;
export const MAKALAM_CHAIN = 25;

const accounts = new Map<Site, SiteAccount>();
const loading = new Map<Site, Promise<void>>();
const listeners = new Set<() => void>();

function setAccount(a: SiteAccount) {
  accounts.set(a.site, a);
  listeners.forEach((fn) => fn());
}

export function loadSite(site: Site, force = false): Promise<void> {
  const pending = loading.get(site);
  if (pending) return pending;
  if (!force && accounts.has(site)) return Promise.resolve();
  const p = get<SiteAccount>(`/crosspost/${site}`)
    .then(setAccount, () => undefined)
    .finally(() => loading.delete(site));
  loading.set(site, p);
  return p;
}

export async function connectSite(site: Site, identifier: string, password: string): Promise<SiteAccount> {
  const a = await put<SiteAccount>(`/crosspost/${site}`, { identifier, password });
  setAccount(a);
  return a;
}

export async function disconnectSite(site: Site): Promise<void> {
  setAccount(await del<SiteAccount>(`/crosspost/${site}`));
}

/** The owner's accounts on every site, loaded on first use. */
export function useSites(): Partial<Record<Site, SiteAccount>> {
  const [, rerender] = useState(0);
  useEffect(() => {
    const fn = () => rerender((n) => n + 1);
    listeners.add(fn);
    SITES.forEach((s) => loadSite(s));
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return Object.fromEntries(accounts) as Partial<Record<Site, SiteAccount>>;
}

export const busy = (c: Crosspost) => c.status === "pending" || c.status === "sending";
export const crosspostsOf = (t: Thought): Crosspost[] => t.crossposts ?? [];
