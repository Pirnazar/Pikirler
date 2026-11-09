import { useState, type FormEvent } from "react";
import { API_PREFIX, errorMessage, patch, put, type Settings } from "../api/client";
import { useApp } from "../app/context";
import { Header } from "../components/Header";
import { LangSwitch } from "../components/LangSwitch";
import { useI18n, type I18n } from "../i18n";
import { connectTeswirler, disconnectTeswirler, useTeswirler } from "../lib/crosspost";

type Providers = Record<string, { provider?: string; model?: string | null; external?: boolean }>;

const SHORTCUTS = [["N", "k_new"], ["/", "k_search"], ["Esc", "k_esc"], ["Ctrl + Enter", "k_publish"]] as const;

function describe(t: I18n["t"], p?: { provider?: string; model?: string | null; external?: boolean }): string {
  if (!p || p.provider === "disabled") return t("not_configured");
  return `${p.provider}${p.model ? " · " + p.model : ""} ${p.external ? t("p_external") : t("p_local")}`;
}

export function SettingsView() {
  const app = useApp();
  const { t } = useI18n();
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
    if (v !== (s?.display_name ?? "")) update({ display_name: v }, t("name_saved"));
  };

  return (
    <>
      <Header title={t("nav_settings")} />
      <div className="pk-settings">
        <div className="field">
          <label htmlFor="pk-name">{t("your_name")}</label>
          <input id="pk-name" className="input" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
            onBlur={saveName} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
        </div>

        <div>
          <div className="pk-section-label" style={{ marginBottom: 8 }}>{t("language")}</div>
          <LangSwitch large />
        </div>

        <div>
          <div className="pk-section-label" style={{ marginBottom: 8 }}>{t("appearance")}</div>
          <div role="group" className="pk-seg lg">
            {(["light", "dark"] as const).map((k) => (
              <button key={k} aria-pressed={app.theme === k} onClick={() => app.setTheme(k)}>{k === "light" ? t("light") : t("dark")}</button>
            ))}
          </div>
        </div>

        <div style={{ borderTop: "2px solid var(--color-divider)" }}>
          <button role="switch" aria-checked={!!s?.ai_external_enabled} className="pk-switch" disabled={!s}
            onClick={() => update({ ai_external_enabled: !s?.ai_external_enabled }, s?.ai_external_enabled ? t("ai_turned_off") : t("ai_turned_on"))}>
            <span style={{ flex: 1 }}>
              <span style={{ display: "block", fontWeight: 800 }}>{t("ai_features")}</span>
              <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>
                {t("ai_features_desc")} {anyExternal ? t("ai_external_desc") : t("ai_local_desc")}
              </span>
              <span style={{ display: "block", fontSize: 12, color: "var(--color-neutral-600)", marginTop: 6 }}>
                {t("p_llm")}: {describe(t, providers.llm)} · {t("p_embeddings")}: {describe(t, providers.embeddings)} · {t("p_speech")}: {describe(t, providers.asr)}
              </span>
            </span>
            <span className="track"><span className="knob" /></span>
          </button>
          <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "14px 0", borderBottom: "1px solid var(--color-divider)" }}>
            <span style={{ flex: 1 }}>
              <span style={{ display: "block", fontWeight: 800 }}>{t("timezone")}</span>
              <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>
                {t("timezone_desc", { tz: s?.timezone ?? "…" })}
              </span>
            </span>
            {s && s.timezone !== deviceTz && (
              <button className="btn btn-secondary" onClick={() => update({ timezone: deviceTz }, t("tz_updated"))}>{t("use_tz", { tz: deviceTz })}</button>
            )}
          </div>
        </div>

        <MagicWord enabled={!!s?.magic_word_enabled} />

        <Sharing />

        <div>
          <div className="pk-section-label" style={{ marginBottom: 6 }}>{t("keyboard")}</div>
          {SHORTCUTS.map(([k, d]) => <div key={k} className="pk-shortcut"><span>{t(d)}</span><span>{k}</span></div>)}
        </div>

        <div>
          <div className="pk-section-label" style={{ marginBottom: 8 }}>{t("your_data")}</div>
          <p className="pk-muted" style={{ fontSize: 13 }}>{t("your_data_desc")}</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn btn-secondary" href={API_PREFIX + "/export/json"} download>{t("export_json")}</a>
            <a className="btn btn-secondary" href={API_PREFIX + "/export/markdown"} download>{t("export_md")}</a>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {s?.magic_word_enabled && <button className="btn btn-secondary" onClick={app.lock}>{t("lock_space")}</button>}
          <button className="btn btn-ghost" onClick={app.signOut}>{t("sign_out")}</button>
        </div>
      </div>
    </>
  );
}

function MagicWord({ enabled }: { enabled: boolean }) {
  const app = useApp();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: FormEvent, remove = false) {
    e.preventDefault();
    setErr("");
    if (!password) return setErr(t("need_password"));
    if (!remove && word.length < 4) return setErr(t("magic_too_short"));
    setBusy(true);
    try {
      await put("/auth/magic-word", { password, magic_word: remove ? null : word });
      app.setSettings({ ...app.settings!, magic_word_enabled: !remove });
      app.toast(remove ? t("magic_removed") : t("magic_saved"));
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
      <div className="pk-section-label" style={{ marginBottom: 6 }}>{t("magic_word")}</div>
      <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>
        {t("magic_desc")} {enabled ? t("magic_on") : t("magic_off")}
      </p>
      {!open ? (
        <button className="btn btn-secondary" onClick={() => setOpen(true)}>{enabled ? (app.authMode === "magic_word" ? t("edit") : t("change_or_remove")) : t("set_magic")}</button>
      ) : (
        <form onSubmit={(e) => submit(e)} style={{ display: "grid", gap: 10 }}>
          <div className="pk-form-row">
            <div className="field"><label htmlFor="mw-pass">{t("password")}</label>
              <input id="mw-pass" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            <div className="field"><label htmlFor="mw-word">{t("new_magic")}</label>
              <input id="mw-word" className="input" type="password" autoComplete="new-password" value={word} onChange={(e) => setWord(e.target.value)} /></div>
          </div>
          {err && <div role="alert" className="pk-error">{err}</div>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn-primary" type="submit" disabled={busy}>{t("save")}</button>
            {enabled && app.authMode !== "magic_word" && <button className="btn btn-secondary" type="button" disabled={busy} onClick={(e) => submit(e, true)}>{t("remove")}</button>}
            <button className="btn btn-ghost" type="button" onClick={() => setOpen(false)}>{t("cancel")}</button>
          </div>
        </form>
      )}
    </div>
  );
}

/** Where else a thought can be published. X needs no account here: it opens
 *  with the text filled in. Teswirler is signed in once; the password is not kept. */
function Sharing() {
  const app = useApp();
  const { t } = useI18n();
  const account = useTeswirler();
  const [open, setOpen] = useState(false);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function connect(e: FormEvent) {
    e.preventDefault();
    setErr("");
    if (!identifier.trim() || !password) return setErr(t("teswirler_need_login"));
    setBusy(true);
    try {
      await connectTeswirler(identifier.trim(), password);
      app.toast(t("teswirler_connected_toast"));
      setOpen(false);
      setIdentifier("");
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await disconnectTeswirler();
      app.toast(t("teswirler_disconnected_toast"));
    } catch (e2) {
      app.toast(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="pk-section-label" style={{ marginBottom: 6 }}>{t("sharing")}</div>
      <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>{t("sharing_desc")}</p>
      <div style={{ padding: "12px 0", borderBottom: "1px solid var(--color-divider)" }}>
        <span style={{ display: "block", fontWeight: 800 }}>X</span>
        <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>{t("x_desc")}</span>
      </div>
      <div style={{ padding: "12px 0", display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1, minWidth: 200 }}>
            <span style={{ display: "block", fontWeight: 800 }}>Teswirler</span>
            <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-700)", marginTop: 2 }}>
              {account?.connected ? (
                <>
                  {t("teswirler_connected_as")}{" "}
                  {account.profile_url ? <a href={account.profile_url} target="_blank" rel="noopener noreferrer">@{account.username}</a> : account.display_name}
                </>
              ) : t("teswirler_desc")}
            </span>
          </span>
          {account?.connected ? (
            <button className="btn btn-secondary" disabled={busy} onClick={disconnect}>{t("teswirler_disconnect")}</button>
          ) : !open ? (
            <button className="btn btn-secondary" onClick={() => setOpen(true)}>{t("teswirler_connect")}</button>
          ) : null}
        </div>
        {!account?.connected && open && (
          <form onSubmit={connect} style={{ display: "grid", gap: 10 }}>
            <div className="pk-form-row">
              <div className="field"><label htmlFor="ts-id">{t("teswirler_login")}</label>
                <input id="ts-id" className="input" autoComplete="off" value={identifier} onChange={(e) => setIdentifier(e.target.value)} /></div>
              <div className="field"><label htmlFor="ts-pass">{t("teswirler_password")}</label>
                <input id="ts-pass" className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            </div>
            <p className="pk-muted" style={{ fontSize: 12, margin: 0 }}>{t("teswirler_password_note")}</p>
            {err && <div role="alert" className="pk-error">{err}</div>}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? t("teswirler_connecting") : t("teswirler_connect")}</button>
              <button className="btn btn-ghost" type="button" onClick={() => { setOpen(false); setErr(""); setPassword(""); }}>{t("cancel")}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
