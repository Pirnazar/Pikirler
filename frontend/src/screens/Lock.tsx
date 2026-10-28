import { useState, type FormEvent } from "react";
import { ApiError, errorMessage, request, type Session } from "../api/client";
import { I, Icon } from "../lib/icons";

export type LockMode = "login" | "magic";

/** Sign-in and Magic Word unlock. The password signs the owner in; the Magic
 *  Word only unlocks an already authenticated session (HTTP 423 until then). */
export function Lock({ mode, leaving, onReady, onModeChange }: {
  mode: LockMode;
  leaving: boolean;
  onReady: (s: Session) => void;
  onModeChange: (m: LockMode) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [magic, setMagic] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (mode === "login" && (!email.trim() || !password)) return setError("Enter your email and password.");
    if (mode === "magic" && !magic.trim()) return setError("Enter your magic word to open the space.");
    setBusy(true);
    try {
      if (mode === "login") {
        const s = await request<Session>("POST", "/auth/login", { json: { email: email.trim(), password }, quietAuth: true });
        setPassword("");
        if (s.magic_word_enabled && !s.unlocked) onModeChange("magic");
        else onReady(s);
      } else {
        await request("POST", "/auth/unlock", { json: { magic_word: magic }, quietAuth: true });
        setMagic("");
        onReady(await request<Session>("GET", "/auth/me", { quietAuth: true }));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && mode === "magic" && err.code !== "invalid_magic_word") {
        onModeChange("login");
      }
      setError(
        err instanceof ApiError && err.code === "invalid_credentials"
          ? "Wrong email or password."
          : err instanceof ApiError && err.code === "invalid_magic_word"
            ? "That's not your magic word."
            : errorMessage(err),
      );
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      await request("POST", "/auth/logout", { quietAuth: true });
    } catch {
      /* already signed out */
    }
    setError("");
    onModeChange("login");
  }

  return (
    <div className={"pk-lock" + (leaving ? " leaving" : "")}>
      <div className="pk-lock-art">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontWeight: 600, letterSpacing: ".1em", textTransform: "uppercase" }}>
          <span>Second Brain</span>
          <span>Private</span>
        </div>
        <div>
          <div className="pk-lock-word">Pikirler</div>
          <div className="pk-lock-rule" />
          <div className="pk-lock-tag">Your thoughts. Your timeline. Your second brain.</div>
        </div>
      </div>
      <form className="pk-lock-form" onSubmit={submit} noValidate>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Icon d={I.lock} className="accent-stroke" />
          <h2 style={{ fontSize: 28, margin: 0 }}>Your mind, your space.</h2>
        </div>
        {mode === "login" ? (
          <>
            <div className="field">
              <label htmlFor="pk-email">Email</label>
              <input id="pk-email" className="input" type="email" autoComplete="username" value={email}
                onChange={(e) => { setEmail(e.target.value); setError(""); }} autoFocus />
            </div>
            <div className="field">
              <label htmlFor="pk-password">Password</label>
              <input id="pk-password" className="input" type="password" autoComplete="current-password" placeholder="••••••••"
                value={password} onChange={(e) => { setPassword(e.target.value); setError(""); }} />
            </div>
          </>
        ) : (
          <div className="field">
            <label htmlFor="pk-magic">Magic word</label>
            <input id="pk-magic" className="input" type="password" autoComplete="off" placeholder="••••••••" value={magic}
              onChange={(e) => { setMagic(e.target.value); setError(""); }} autoFocus />
          </div>
        )}
        {error && <div role="alert" className="pk-error">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {mode === "login" ? (busy ? "Signing in…" : "Sign in") : busy ? "Unlocking…" : "Unlock"}
          <Icon d={I.arrow} size="sm" />
        </button>
        <p style={{ fontSize: 12, color: "var(--color-neutral-600)", margin: 0, textWrap: "pretty" } as React.CSSProperties}>
          {mode === "login"
            ? "Only the owner can sign in. There is no public sign-up."
            : "Your session is signed in; the magic word is an extra lock on top of it, never the only one."}
        </p>
        {mode === "magic" && (
          <button type="button" className="btn btn-ghost" onClick={signOut} style={{ alignSelf: "flex-start" }}>
            Sign out instead
          </button>
        )}
      </form>
    </div>
  );
}
