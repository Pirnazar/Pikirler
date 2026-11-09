import { useEffect, useState } from "react";
import { del, get, put, type Thought } from "../api/client";
import type { components } from "../api/schema";

export type TeswirlerAccount = components["schemas"]["TeswirlerAccountOut"];
export type Crosspost = components["schemas"]["CrosspostOut"];
export type ShareTarget = "x" | "teswirler";

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

// --- Teswirler account: one shared, lazily loaded copy for the whole app ---

let account: TeswirlerAccount | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<(a: TeswirlerAccount | null) => void>();

function set(a: TeswirlerAccount | null) {
  account = a;
  listeners.forEach((fn) => fn(a));
}

export function loadTeswirler(force = false): Promise<void> {
  if (!force && (account || loading)) return loading ?? Promise.resolve();
  loading = get<TeswirlerAccount>("/crosspost/teswirler")
    .then(set, () => undefined)
    .finally(() => {
      loading = null;
    });
  return loading;
}

export async function connectTeswirler(identifier: string, password: string): Promise<TeswirlerAccount> {
  const a = await put<TeswirlerAccount>("/crosspost/teswirler", { identifier, password });
  set(a);
  return a;
}

export async function disconnectTeswirler(): Promise<void> {
  set(await del<TeswirlerAccount>("/crosspost/teswirler"));
}

export function useTeswirler(): TeswirlerAccount | null {
  const [a, setA] = useState(account);
  useEffect(() => {
    listeners.add(setA);
    loadTeswirler();
    return () => {
      listeners.delete(setA);
    };
  }, []);
  return a;
}

export const busy = (c: Crosspost) => c.status === "pending" || c.status === "sending";
export const crosspostsOf = (t: Thought): Crosspost[] => t.crossposts ?? [];
