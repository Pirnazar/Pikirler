import { useState, type FormEvent } from "react";
import { API_PREFIX, errorMessage, patch, put, type Settings } from "../api/client";
import { useApp } from "../app/context";
import { Header } from "../components/Header";

type Providers = Record<string, { provider?: string; model?: string | null; external?: boolean }>;

const SHORTCUTS: [string, string][] = [["N", "New thought"], ["/", "Search"], ["Esc", "Close or go back"], ["Ctrl + Enter", "Publish while writing"]];

function describe(p?: { provider?: string; model?: string | null; external?: boolean }): string {
  if (!p || p.provider === "disabled") return "not configured";
  return `${p.provider}${p.model ? " · " + p.model : ""}${p.external ? " (external)" : " (your server)"}`;
}

export function SettingsView() {
  const app = useApp();
  const s = app.settings;
  const [name, setName] = useState(s?.display_name ?? app.name);
  const deviceTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const providers = (s?.providers ?? {}) as Providers;
  const anyExternal = Object.values(providers).some((p) => p.external && p.provider !== "disabled");

  async function update(body: Record<string, unknown>, done?: string) {
    try {
      const next = await patch<Settings>("/settings", body);
      app.setSettings(next);
      if (done) app.toast(done);
    } catch (e) {
      app.toast(errorMessage(e));
    }
  }

  const saveName = () => {
    const v = name.trim();
    if (v !== (s?.display_name ?? "")) update({ display_name: v }, "Name saved");
  };

  return (
    <>
      <Header title="Settings" />
      <div className="pk-settings">
        <div className="field">
          <label htmlFor="pk-name">Your name</label>
          <input id="pk-name" className="input" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
            onBlur={saveName} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
        </div>

        <div>
          <div className="pk-section-label" style={{ marginBottom: 8 }}>Appearance</div>
          <div role="group" className="pk-seg lg">
            {(["light", "dark"] as const).map((k) => (
              <button key={k} aria-pressed={app.theme === k} onClick={() => app.setTheme(k)}>{k === "light" ? "Light" : "Dark"}</button>
            ))}
          </div>
        </div>

        <div style={{ borderTop: "2px solid var(--color-divider)" }}>
          <button role="switch" aria-checked={!!s?.ai_external_enabled} className="pk-switch" disabled={!s}
            onClick={() => update({ ai_external_enabled: !s?.ai_external_enabled }, s?.ai_external_enabled ? "AI features turned off" : "AI features turned on")}>
            <span style={{ flex: 1 }}>
              <span style={{ display: "block", fontWeight: 800 }}>AI features</span>
              <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>
                Search by meaning, insights and transcription. {anyExternal
                  ? "When on, only the thoughts a request needs are sent to the external provider — never the whole journal. Off means nothing leaves your server."
                  : "All configured providers run on your own server."}
              </span>
              <span style={{ display: "block", fontSize: 12, color: "var(--color-neutral-600)", marginTop: 6 }}>
                Language model: {describe(providers.llm)} · Embeddings: {describe(providers.embeddings)} · Speech: {describe(providers.asr)}
              </span>
            </span>
            <span className="track"><span className="knob" /></span>
          </button>
          <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "14px 0", borderBottom: "1px solid var(--color-divider)" }}>
            <span style={{ flex: 1 }}>
              <span style={{ display: "block", fontWeight: 800 }}>Time zone</span>
              <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>
                {s?.timezone ?? "…"} — used for On this day, the calendar and “last month” in questions.
              </span>
            </span>
            {s && s.timezone !== deviceTz && (
              <button className="btn btn-secondary" onClick={() => update({ timezone: deviceTz }, "Time zone updated")}>Use {deviceTz}</button>
            )}
          </div>
        </div>

        <MagicWord enabled={!!s?.magic_word_enabled} />

        <div>
          <div className="pk-section-label" style={{ marginBottom: 6 }}>Keyboard</div>
          {SHORTCUTS.map(([k, d]) => <div key={k} className="pk-shortcut"><span>{d}</span><span>{k}</span></div>)}
        </div>

        <div>
          <div className="pk-section-label" style={{ marginBottom: 8 }}>Your data</div>
          <p className="pk-muted" style={{ fontSize: 13 }}>Everything you wrote, in open formats. Downloads go straight to this device.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn btn-secondary" href={API_PREFIX + "/export/json"} download>Export JSON</a>
            <a className="btn btn-secondary" href={API_PREFIX + "/export/markdown"} download>Export Markdown</a>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {s?.magic_word_enabled && <button className="btn btn-secondary" onClick={app.lock}>Lock space</button>}
          <button className="btn btn-ghost" onClick={app.signOut}>Sign out</button>
        </div>
      </div>
    </>
  );
}

function MagicWord({ enabled }: { enabled: boolean }) {
  const app = useApp();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: FormEvent, remove = false) {
    e.preventDefault();
    setErr("");
    if (!password) return setErr("Enter your password to confirm.");
    if (!remove && word.length < 4) return setErr("Use at least 4 characters.");
    setBusy(true);
    try {
      await put("/auth/magic-word", { password, magic_word: remove ? null : word });
      app.setSettings({ ...app.settings!, magic_word_enabled: !remove });
      app.toast(remove ? "Magic word removed" : "Magic word saved");
      setOpen(false);
      setPassword("");
      setWord("");
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="pk-section-label" style={{ marginBottom: 6 }}>Magic word</div>
      <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>
        An extra lock on top of your password: after signing in, Pikirler stays closed until you type it.
        {enabled ? " It is on." : " It is off."}
      </p>
      {!open ? (
        <button className="btn btn-secondary" onClick={() => setOpen(true)}>{enabled ? "Change or remove" : "Set a magic word"}</button>
      ) : (
        <form onSubmit={(e) => submit(e)} style={{ display: "grid", gap: 10 }}>
          <div className="pk-form-row">
            <div className="field"><label htmlFor="mw-pass">Password</label>
              <input id="mw-pass" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            <div className="field"><label htmlFor="mw-word">New magic word</label>
              <input id="mw-word" className="input" type="password" autoComplete="new-password" value={word} onChange={(e) => setWord(e.target.value)} /></div>
          </div>
          {err && <div role="alert" className="pk-error">{err}</div>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn-primary" type="submit" disabled={busy}>Save</button>
            {enabled && <button className="btn btn-secondary" type="button" disabled={busy} onClick={(e) => submit(e, true)}>Remove</button>}
            <button className="btn btn-ghost" type="button" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}
