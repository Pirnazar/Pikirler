import type { components } from "./schema";

type S = components["schemas"];
export type Thought = S["ThoughtOut"];
export type Attachment = S["AttachmentRef"];
export type Session = S["SessionState"];
export type Settings = S["SettingsOut"];
export type Tag = S["TagOut"];
export type Project = S["ProjectOut"];
export type Relation = S["RelationOut"];
export type Revision = S["RevisionOut"];
export type Thread = S["ThreadOut"];
export type SearchResponse = S["SearchResponse"];
export type SearchResult = S["SearchResult"];
export type SourceRef = S["SourceRef"];
export type AskResponse = S["AskResponse"];
export type SummaryResponse = S["SummaryResponse"];
export type ExpandResponse = S["ExpandResponse"];
export type ActionsResponse = S["ActionsResponse"];
export type EvolutionResponse = S["EvolutionResponse"];
export type RelatedResponse = S["RelatedResponse"];
export type OnThisDay = S["OnThisDay"];
export type MonthlyRecap = S["MonthlyRecap"];
export type Activity = S["Activity"];
export type ThoughtType = S["ThoughtType"];
export type Page<T> = { items: T[]; next_cursor?: string | null; pinned?: T[] };

export const API_PREFIX = "/api/v1/pikirler";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly retryAfter: number | null = null,
  ) {
    super(message);
  }
}

/** Fired when the server says the session is gone (401) or locked (423). */
export const AUTH_EVENT = "pikirler:auth";
export type AuthEventDetail = "unauthorized" | "locked";

function csrfToken(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)pikirler_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

async function toError(resp: Response): Promise<ApiError> {
  let code = "http_" + resp.status;
  let message = resp.statusText || "Request failed";
  let details: Record<string, unknown> = {};
  try {
    const body = await resp.json();
    if (body?.error) ({ code, message, details = {} } = body.error);
  } catch {
    /* non-JSON error body (proxy, network) */
  }
  const ra = resp.headers.get("Retry-After");
  return new ApiError(resp.status, code, message, details, ra ? Number(ra) : null);
}

export interface RequestOptions {
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  signal?: AbortSignal;
  /** Auth endpoints handle 401/423 themselves and must not trigger the global lock. */
  quietAuth?: boolean;
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (method !== "GET" && method !== "HEAD") {
    const token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let resp: Response;
  try {
    resp = await fetch(API_PREFIX + withQuery(path, opts.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "Can't reach Pikirler. Check your connection.");
  }
  if (!resp.ok) {
    const err = await toError(resp);
    if (!opts.quietAuth && (resp.status === 401 || resp.status === 423)) {
      window.dispatchEvent(
        new CustomEvent<AuthEventDetail>(AUTH_EVENT, { detail: resp.status === 423 ? "locked" : "unauthorized" }),
      );
    }
    throw err;
  }
  if (resp.status === 204) return undefined as T;
  const type = resp.headers.get("Content-Type") || "";
  return (type.includes("json") ? resp.json() : resp.text()) as Promise<T>;
}

export const get = <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>("GET", path, { query, signal });
export const post = <T>(path: string, json?: unknown) => request<T>("POST", path, { json: json ?? {} });
export const patch = <T>(path: string, json: unknown) => request<T>("PATCH", path, { json });
export const put = <T>(path: string, json: unknown) => request<T>("PUT", path, { json });
export const del = <T = void>(path: string) => request<T>("DELETE", path);

/** Human message for an error from any call. */
export function errorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Try again.";
  switch (e.code) {
    case "network":
      return e.message;
    case "external_ai_disabled":
      return "AI is off. Turn on “AI features” in Settings to let Pikirler send selected thoughts to the AI provider.";
    case "ai_disabled":
    case "embeddings_disabled":
      return "AI is not configured on the server. Your thoughts are untouched.";
    case "asr_disabled":
      return "Transcription is not configured on the server. The audio is kept.";
    case "provider_unavailable":
      return "Couldn't reach the AI right now. Try again in a moment.";
    case "ai_quota_exceeded":
      return "Today's AI limit is reached. It resets tomorrow.";
    case "ai_bad_response":
      return "The AI answered in an unexpected way. Try again.";
    case "rate_limited":
    case "account_locked":
      return e.retryAfter ? `Too many attempts. Try again in ${Math.ceil(e.retryAfter / 60)} min.` : "Too many attempts. Try again later.";
    case "version_conflict":
      return "This thought was changed elsewhere. Reload and try again.";
    default:
      return e.message;
  }
}

export function mediaSrc(url: string | null | undefined): string {
  return url ?? "";
}
