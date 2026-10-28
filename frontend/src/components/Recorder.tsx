import { useEffect, useRef, useState } from "react";
import { errorMessage, request, type Settings } from "../api/client";
import type { components } from "../api/schema";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useI18n, type I18n } from "../i18n";
import { LANGS, mmss } from "../lib/format";

type VoiceNoteOut = components["schemas"]["VoiceNoteOut"];
type Status = "starting" | "rec" | "paused" | "stopped" | "denied";

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

function asrNote(settings: Settings | null, t: I18n["t"]): string {
  const asr = (settings?.providers as Record<string, { provider?: string; external?: boolean }> | undefined)?.asr;
  if (!asr || asr.provider === "disabled") return t("asr_off");
  if (asr.external && !settings?.ai_external_enabled) return t("asr_needs_consent");
  return t("asr_on");
}

/** Voice thought: records with MediaRecorder and uploads the audio as-is.
 *  Transcription happens on the owner's server, not in a browser speech API,
 *  so nothing is sent to a third party from here. */
export function Recorder({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const { t } = useI18n();
  const [status, setStatus] = useState<Status>("starting");
  const [secs, setSecs] = useState(0);
  const [levels, setLevels] = useState<number[]>(() => Array(44).fill(0.05));
  const [lang, setLang] = useState<"auto" | "tk" | "ru" | "en" | "zh">("auto");
  const [note, setNote] = useState("");
  const [blob, setBlob] = useState<Blob | null>(null);
  const [src, setSrc] = useState("");
  const [saving, setSaving] = useState(false);
  const mr = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const actx = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const statusRef = useRef<Status>("starting");
  statusRef.current = status;

  useEffect(() => {
    let cancelled = false;
    navigator.mediaDevices?.getUserMedia({ audio: true }).then(
      (st) => {
        if (cancelled) return st.getTracks().forEach((t) => t.stop());
        stream.current = st;
        try {
          const ctx = new AudioContext();
          const an = ctx.createAnalyser();
          an.fftSize = 512;
          ctx.createMediaStreamSource(st).connect(an);
          actx.current = ctx;
          analyser.current = an;
        } catch {
          /* level meter is optional */
        }
        const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
        const rec = new MediaRecorder(st, mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = (ev) => ev.data.size && chunks.current.push(ev.data);
        rec.onstop = () => {
          const b = new Blob(chunks.current, { type: rec.mimeType || "audio/webm" });
          setBlob(b);
          setSrc(URL.createObjectURL(b));
          setStatus("stopped");
          cleanup();
        };
        rec.start(1000);
        mr.current = rec;
        setStatus("rec");
      },
      () => !cancelled && setStatus("denied"),
    ) ?? setStatus("denied");
    const iv = window.setInterval(() => {
      if (statusRef.current !== "rec") return;
      setSecs((s) => s + 0.1);
      let lv = 0.05;
      const an = analyser.current;
      if (an) {
        const a = new Uint8Array(an.fftSize);
        an.getByteTimeDomainData(a);
        let sum = 0;
        for (const v of a) sum += ((v - 128) / 128) ** 2;
        lv = Math.max(0.05, Math.min(1, Math.sqrt(sum / a.length) * 5));
      }
      setLevels((l) => [...l.slice(1), lv]);
    }, 100);
    return () => {
      cancelled = true;
      window.clearInterval(iv);
      if (mr.current && mr.current.state !== "inactive") {
        mr.current.onstop = null;
        mr.current.stop();
      }
      cleanup();
    };
  }, []);

  useEffect(() => () => { if (src) URL.revokeObjectURL(src); }, [src]);

  function cleanup() {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    actx.current?.close().catch(() => {});
    actx.current = null;
    analyser.current = null;
  }

  async function save() {
    if (!blob) return;
    setSaving(true);
    const type = blob.type.split(";")[0] || "audio/webm";
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    const fd = new FormData();
    fd.append("file", new File([blob], `voice-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.${ext}`, { type }));
    if (lang !== "auto") fd.append("language", lang);
    if (note.trim()) fd.append("content", note.trim());
    try {
      const r = await request<VoiceNoteOut>("POST", "/voice/notes", { body: fd });
      emit({ type: "created", thought: r.thought });
      app.toast(r.transcription_unavailable ? t("voice_saved_no_asr") : t("voice_saved"));
      onClose();
    } catch (e) {
      app.toast(errorMessage(e));
      setSaving(false);
    }
  }

  const stopped = status === "stopped";
  const label = status === "rec" ? t("rec_recording") : status === "paused" ? t("rec_paused") : stopped ? t("rec_recorded", { time: mmss(secs) })
    : status === "denied" ? t("rec_no_mic") : t("rec_starting");

  return (
    <div className="dialog-backdrop">
      <div role="dialog" aria-modal="true" aria-label={t("voice_thought")} className="dialog" style={{ maxWidth: 460 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 800, letterSpacing: ".1em", textTransform: "uppercase" }}>
          <span className={"pk-rec-dot" + (status === "rec" ? " live" : "")} style={stopped ? { background: "var(--color-text)" } : undefined} />
          <span style={{ flex: 1 }}>{label}</span>
        </div>
        <div className="pk-rec-time">{mmss(secs)}<span>.{Math.floor((secs * 10) % 10)}</span></div>
        <div aria-hidden="true" className={"pk-rec-bars" + (stopped ? " done" : "")}>
          {levels.map((b, i) => <span key={i} style={{ height: Math.round(4 + b * 64) }} />)}
        </div>
        {status === "denied" && (
          <p className="pk-error" style={{ margin: 0 }}>{t("rec_allow_mic")}</p>
        )}
        <div role="group" aria-label={t("language")} className="pk-seg">
          {(["auto", "tk", "ru", "en", "zh"] as const).map((k) => (
            <button key={k} aria-pressed={lang === k} onClick={() => setLang(k)}>{k === "auto" ? t("lang_auto") : LANGS[k]}</button>
          ))}
        </div>
        <div className="field">
          <label htmlFor="pk-note">{t("note_optional")}</label>
          <textarea id="pk-note" className="input" rows={3} placeholder={t("note_placeholder")} value={note}
            onChange={(e) => setNote(e.target.value)} style={{ fontSize: 15, minHeight: 72 }} />
        </div>
        <p style={{ fontSize: 12, color: "var(--color-neutral-600)", margin: 0 }}>{asrNote(app.settings, t)}</p>
        {src && <audio controls src={src} style={{ width: "100%" }} />}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
          {status === "rec" && <button className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => { mr.current?.pause(); setStatus("paused"); }}>{t("pause")}</button>}
          {status === "paused" && <button className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => { mr.current?.resume(); setStatus("rec"); }}>{t("resume")}</button>}
          {(status === "rec" || status === "paused") && (
            <button className="btn btn-primary" style={{ minHeight: 44, gap: 8 }} onClick={() => mr.current?.stop()}>
              <span style={{ width: 10, height: 10, background: "currentColor" }} />{t("stop")}
            </button>
          )}
          {stopped && <button className="btn btn-primary" style={{ minHeight: 44 }} disabled={saving} onClick={save}>{saving ? t("saving") : t("save_voice")}</button>}
          <button className="btn btn-ghost" style={{ minHeight: 44, marginLeft: "auto" }} onClick={onClose}>{t("discard")}</button>
        </div>
      </div>
    </div>
  );
}
