import { useEffect, useState } from "react";
import { ApiError, AUTH_EVENT, request, type AuthEventDetail, type Session } from "./api/client";
import { I18nProvider, initialLang, useLangSetter, type Lang } from "./i18n";
import { Lock, type LockMode } from "./screens/Lock";
import { Shell } from "./Shell";

type Phase = { kind: "boot" } | { kind: "locked"; mode: LockMode } | { kind: "ready"; session: Session; leaving: boolean };

const THEME_KEY = "pikirler.theme";

function initialTheme(): "light" | "dark" {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "light" || t === "dark") return t;
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const [phase, setPhase] = useState<Phase>({ kind: "boot" });
  const [theme, setThemeState] = useState(initialTheme);
  const [lang, setLangState] = useState<Lang>(initialLang);
  const setLang = useLangSetter(setLangState);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const setTheme = (t: "light" | "dark") => {
    setThemeState(t);
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* storage unavailable */
    }
  };

  useEffect(() => {
    request<Session>("GET", "/auth/me", { quietAuth: true }).then(
      (s) => setPhase(s.magic_word_enabled && !s.unlocked ? { kind: "locked", mode: "magic" } : { kind: "ready", session: s, leaving: false }),
      (e) => setPhase({ kind: "locked", mode: e instanceof ApiError && e.status === 423 ? "magic" : "login" }),
    );
    const onAuth = (ev: Event) => {
      const d = (ev as CustomEvent<AuthEventDetail>).detail;
      setPhase({ kind: "locked", mode: d === "locked" ? "magic" : "login" });
    };
    window.addEventListener(AUTH_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_EVENT, onAuth);
  }, []);

  const ready = (session: Session) => {
    // The lock panel slides away over the already rendered space.
    setPhase({ kind: "ready", session, leaving: true });
    setTimeout(() => setPhase((p) => (p.kind === "ready" ? { ...p, leaving: false } : p)), 820);
  };

  return (
    <I18nProvider lang={lang} setLang={setLang}>
      {phase.kind === "boot" ? (
        <div className="pk-boot pk-loading-line">Pikirler</div>
      ) : (
        <div className="pk-root">
          {phase.kind === "ready" && (
            <Shell
              session={phase.session}
              theme={theme}
              setTheme={setTheme}
              onLocked={(mode) => setPhase({ kind: "locked", mode })}
            />
          )}
          {(phase.kind === "locked" || phase.leaving) && (
            <Lock
              mode={phase.kind === "locked" ? phase.mode : "magic"}
              leaving={phase.kind === "ready" && phase.leaving}
              onReady={ready}
              onModeChange={(mode) => setPhase({ kind: "locked", mode })}
            />
          )}
        </div>
      )}
    </I18nProvider>
  );
}
