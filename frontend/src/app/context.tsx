import { createContext, useContext } from "react";
import type { Project, Session, Settings, Tag, Thought } from "../api/client";

export type View =
  | "home" | "mine" | "bookmarks" | "topics" | "projects" | "project"
  | "memories" | "search" | "calendar" | "settings" | "detail";

export interface Route {
  view: View;
  /** detail/project: an id; topics: a tag; calendar: YYYY-MM; search: a question. */
  arg?: string;
}

const VIEWS: View[] = ["home", "mine", "bookmarks", "topics", "projects", "project", "memories", "search", "calendar", "settings", "detail"];

export function parseHash(hash = window.location.hash): Route {
  const [view, ...rest] = hash.replace(/^#\/?/, "").split("/");
  const v = (VIEWS as string[]).includes(view) ? (view as View) : "home";
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : undefined;
  return { view: v, arg };
}

export function routeHash(view: View, arg?: string): string {
  return "#/" + view + (arg ? "/" + encodeURIComponent(arg) : "");
}

export interface ComposerPreset {
  replyTo?: Thought | null;
  projectId?: string;
}

export interface AppApi {
  session: Session;
  settings: Settings | null;
  setSettings: (s: Settings) => void;
  name: string;
  initial: string;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
  route: Route;
  go: (view: View, arg?: string) => void;
  back: () => void;
  isMobile: boolean;
  projects: Project[];
  tags: Tag[];
  reloadMeta: () => void;
  toast: (msg: string, undo?: () => void) => void;
  newThought: (preset?: ComposerPreset) => void;
  reply: (t: Thought) => void;
  composer: ComposerPreset & { focusToken: number };
  clearComposerPreset: () => void;
  openRecorder: () => void;
  openConnect: (t: Thought) => void;
  askDelete: (t: Thought) => void;
  toggleBookmark: (t: Thought) => Promise<void>;
  /** The configured LLM can be used: a provider exists and, if external, the owner consented. */
  aiAvailable: boolean;
  lock: () => void;
  signOut: () => void;
}

export const AppContext = createContext<AppApi | null>(null);

export function useApp(): AppApi {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppContext");
  return ctx;
}

export const TYPE_LABEL: Record<string, string> = {
  thought: "Thought", idea: "Idea", reflection: "Reflection", journal: "Journal", project: "Project",
};
