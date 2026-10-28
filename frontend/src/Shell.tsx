import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { del, get, post, request, type Project, type Session, type Settings, type Tag, type Thought } from "./api/client";
import { emit } from "./app/bus";
import { AppContext, parseHash, routeHash, type AppApi, type ComposerPreset, type Route, type View } from "./app/context";
import { useMedia, useOnline } from "./app/hooks";
import { I, Icon } from "./lib/icons";
import { snip } from "./lib/format";
import { Composer } from "./components/Composer";
import { ConfirmDelete, ConnectDialog } from "./components/Dialogs";
import { Recorder } from "./components/Recorder";
import { RightPanel } from "./components/RightPanel";
import { HomeView } from "./views/Home";
import { MineView, BookmarksView } from "./views/Mine";
import { DetailView } from "./views/Detail";
import { SearchView } from "./views/Search";
import { TopicsView } from "./views/Topics";
import { ProjectsView, ProjectView } from "./views/Projects";
import { MemoriesView } from "./views/Memories";
import { CalendarView } from "./views/Calendar";
import { SettingsView } from "./views/Settings";
import type { LockMode } from "./screens/Lock";

const NAV: [View, string, string][] = [
  ["home", "Home", I.home], ["mine", "My Thoughts", I.user], ["bookmarks", "Bookmarks", I.bookmark],
  ["topics", "Topics", I.hash], ["projects", "Projects", I.folder], ["memories", "Memories", I.history],
  ["search", "AI Search", I.spark], ["calendar", "Calendar", I.calendar], ["settings", "Settings", I.settings],
];
const MOBILE_NAV: [View | "new", string, string][] = [
  ["home", "Home", I.home], ["search", "Search", I.search], ["new", "New", I.plus], ["memories", "Memories", I.history], ["mine", "Profile", I.user],
];

export function Shell({ session, theme, setTheme, onLocked }: {
  session: Session;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
  onLocked: (mode: LockMode) => void;
}) {
  const isMobile = useMedia("(max-width: 719px)");
  const online = useOnline();
  const [route, setRoute] = useState<Route>(() => parseHash());
  const [settings, setSettings] = useState<Settings | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [toastState, setToast] = useState<{ msg: string; undo?: () => void } | null>(null);
  const [composer, setComposer] = useState<ComposerPreset & { focusToken: number }>({ focusToken: 0 });
  const [sheet, setSheet] = useState(false);
  const [recOpen, setRecOpen] = useState(false);
  const [connectFor, setConnectFor] = useState<Thought | null>(null);
  const [deleteFor, setDeleteFor] = useState<Thought | null>(null);
  const toastTimer = useRef<number>();
  const mainRef = useRef<HTMLElement>(null);

  const name = settings?.display_name || session.owner.display_name || session.owner.email.split("@")[0];
  const initial = (name || "P")[0].toUpperCase();

  useEffect(() => {
    const fn = () => {
      setRoute(parseHash());
      mainRef.current?.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);

  const reloadMeta = useCallback(() => {
    get<Project[]>("/projects").then(setProjects, () => {});
    get<Tag[]>("/tags", { sort: "popular", limit: 200 }).then(setTags, () => {});
  }, []);
  useEffect(() => {
    get<Settings>("/settings").then(setSettings, () => {});
    reloadMeta();
  }, [reloadMeta]);

  const go = useCallback((view: View, arg?: string) => {
    const h = routeHash(view, arg);
    setSheet(false);
    if (window.location.hash === h) {
      mainRef.current?.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    window.location.hash = h;
  }, []);
  const back = useCallback(() => {
    if (window.history.length > 1) window.history.back();
    else go("home");
  }, [go]);

  const toast = useCallback((msg: string, undo?: () => void) => {
    window.clearTimeout(toastTimer.current);
    setToast({ msg, undo });
    toastTimer.current = window.setTimeout(() => setToast(null), undo ? 6000 : 2600);
  }, []);

  const newThought = useCallback((preset: ComposerPreset = {}) => {
    setComposer((c) => ({ ...c, ...preset, focusToken: c.focusToken + 1 }));
    if (isMobile) setSheet(true);
    else if (parseHash().view !== "home") go("home");
  }, [isMobile, go]);

  const signOut = useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      onLocked("login");
    }
  }, [onLocked]);

  const lock = useCallback(async () => {
    if (!(settings?.magic_word_enabled ?? session.magic_word_enabled)) return signOut();
    try {
      await post("/auth/lock");
    } finally {
      onLocked("magic");
    }
  }, [settings, session, onLocked, signOut]);

  const toggleBookmark = useCallback(async (t: Thought) => {
    try {
      const updated = await request<Thought>(t.is_bookmarked ? "DELETE" : "POST", `/thoughts/${t.id}/bookmark`);
      emit({ type: "updated", thought: updated });
      if (updated.is_bookmarked) toast("Saved to bookmarks");
    } catch {
      toast("Couldn't update the bookmark");
    }
  }, [toast]);

  const doDelete = useCallback(async (t: Thought) => {
    setDeleteFor(null);
    try {
      await del(`/thoughts/${t.id}`);
    } catch {
      return toast("Couldn't delete the thought");
    }
    emit({ type: "deleted", id: t.id });
    if (parseHash().view === "detail" && parseHash().arg === t.id) back();
    toast("Thought deleted", async () => {
      setToast(null);
      try {
        const restored = await post<Thought>(`/thoughts/${t.id}/restore`);
        emit({ type: "restored", thought: restored });
      } catch {
        toast("Couldn't restore — it is still in the trash under My Thoughts");
      }
    });
  }, [toast, back]);

  const llm = (settings?.providers as Record<string, { provider?: string; external?: boolean }> | undefined)?.llm;
  const aiAvailable = !!llm && llm.provider !== "disabled" && (!llm.external || !!settings?.ai_external_enabled);

  const api: AppApi = {
    session, settings, setSettings, name, initial, theme, setTheme, route, go, back, isMobile,
    projects, tags, reloadMeta, toast, newThought,
    reply: (t) => newThought({ replyTo: t, projectId: t.projects[0]?.id }),
    composer, clearComposerPreset: () => setComposer((c) => ({ focusToken: c.focusToken })),
    openRecorder: () => { setSheet(false); setRecOpen(true); },
    openConnect: setConnectFor, askDelete: setDeleteFor, toggleBookmark, aiAvailable, lock, signOut,
  };

  // Keyboard: N new thought, / search, Esc closes the topmost layer or goes back.
  const apiRef = useRef(api);
  apiRef.current = api;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable;
      if (e.key === "Escape") {
        if (typing) return el.blur();
        const v = parseHash().view;
        if (v === "detail" || v === "project") apiRef.current.back();
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("[aria-modal='true']")) return;
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        apiRef.current.newThought();
      } else if (e.key === "/") {
        e.preventDefault();
        apiRef.current.go("search");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const activeNav: View = route.view === "detail" ? "home" : route.view === "project" ? "projects" : route.view;
  const view = useMemo(() => {
    switch (route.view) {
      case "mine": return <MineView />;
      case "bookmarks": return <BookmarksView />;
      case "topics": return <TopicsView key={route.arg ?? ""} initialTag={route.arg} />;
      case "projects": return <ProjectsView />;
      case "project": return <ProjectView key={route.arg} id={route.arg ?? ""} />;
      case "memories": return <MemoriesView />;
      case "search": return <SearchView key={route.arg ?? ""} initialQuery={route.arg} />;
      case "calendar": return <CalendarView key={route.arg ?? ""} month={route.arg} />;
      case "settings": return <SettingsView />;
      case "detail": return <DetailView key={route.arg} id={route.arg ?? ""} />;
      default: return <HomeView />;
    }
  }, [route]);

  return (
    <AppContext.Provider value={api}>
      <div className="pk-shell">
        <aside className="pk-sidebar" aria-label="Navigation">
          <button className="pk-brand" onClick={() => go("home")} aria-label="Pikirler home">
            <span className="pk-mark">P</span>
            <span className="pk-brand-text pk-hide-compact">
              <span className="pk-brand-name">Pikirler</span>
              <span className="pk-brand-sub">Second Brain</span>
            </span>
          </button>
          <nav className="pk-nav">
            {NAV.map(([k, label, icon]) => (
              <button key={k} className="pk-nav-item" aria-current={activeNav === k ? "page" : undefined} title={label} onClick={() => go(k)}>
                <Icon d={icon} />
                <span className="pk-nav-label pk-hide-compact">{label}</span>
                {k === "search" && <span className="pk-nav-count pk-hide-compact">/</span>}
              </button>
            ))}
          </nav>
          <button className="btn btn-primary pk-new-btn" onClick={() => newThought()} title="New thought (N)">
            <Icon d={I.plus} size="sm" />
            <span className="pk-hide-compact" style={{ flex: 1, textAlign: "left" }}>New thought</span>
            <span className="pk-kbd pk-hide-compact">N</span>
          </button>
          <div style={{ flex: 1, minHeight: 20 }} />
          <div className="pk-private pk-hide-compact">
            <Icon d={I.lock} size="sm" className="accent-stroke" />
            <span style={{ flex: 1 }}><strong>Private space</strong><span className="pk-muted">Only you can read this</span></span>
          </div>
          <button className="pk-nav-item" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title={theme === "dark" ? "Light mode" : "Dark mode"}>
            <Icon d={theme === "dark" ? I.sun : I.moon} />
            <span className="pk-hide-compact">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
          </button>
          <div className="pk-me">
            <button className="pk-avatar-btn" onClick={() => go("settings")} aria-label="Profile and settings">{initial}</button>
            <span className="pk-hide-compact" style={{ flex: 1, minWidth: 0, lineHeight: 1.2 }}>
              <span className="pk-me-name">{name}</span>
              <span className="pk-muted" style={{ fontSize: 12 }}>Owner</span>
            </span>
            <button className="pk-square-btn sm pk-hide-compact" onClick={lock} title="Lock space" aria-label="Lock space">
              <Icon d={I.lock} size="sm" />
            </button>
          </div>
        </aside>

        <main className="pk-main" ref={mainRef}>
          <div className="pk-col">
            {!online && (
              <div role="status" className="pk-banner">
                <Icon d={I.offline} size="sm" />
                <span style={{ flex: 1 }}>Offline. Your draft stays on this device; publishing and AI resume when you are back online.</span>
              </div>
            )}
            {view}
          </div>
        </main>

        {!isMobile && <RightPanel />}

        {isMobile && (
          <nav className="pk-mobile-nav" aria-label="Main">
            {MOBILE_NAV.map(([k, label, icon]) => (
              <button key={k} aria-label={label} aria-current={k !== "new" && activeNav === k ? "page" : undefined}
                onClick={() => (k === "new" ? newThought() : go(k))}>
                {k === "new" ? (
                  <span className="pk-mobile-new"><Icon d={I.plus} /></span>
                ) : (
                  <><Icon d={icon} /><span>{label}</span></>
                )}
              </button>
            ))}
          </nav>
        )}

        {sheet && isMobile && <Composer variant="sheet" onClose={() => { setSheet(false); api.clearComposerPreset(); }} />}
        {recOpen && <Recorder onClose={() => setRecOpen(false)} />}
        {connectFor && <ConnectDialog thought={connectFor} onClose={() => setConnectFor(null)} />}
        {deleteFor && <ConfirmDelete snippet={snip(deleteFor.content || "Voice note", 120)} onConfirm={() => doDelete(deleteFor)} onCancel={() => setDeleteFor(null)} />}
        {toastState && (
          <div role="status" aria-live="polite" className="pk-toast">
            <span>{toastState.msg}</span>
            {toastState.undo && <button onClick={toastState.undo}>Undo</button>}
          </div>
        )}
      </div>
    </AppContext.Provider>
  );
}
