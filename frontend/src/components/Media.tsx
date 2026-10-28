import { useEffect, useRef, useState } from "react";
import type { Attachment } from "../api/client";
import { fileSize, firstLink, LANGS, mmss } from "../lib/format";
import { useI18n } from "../i18n";
import { I, SolidIcon } from "../lib/icons";

function bars(seed: string, n = 40): number[] {
  let h = 7;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    h = (h * 1103515245 + 12345) >>> 0;
    out.push(5 + (0.15 + ((h >>> 16) % 100) / 118) * 20);
  }
  return out;
}

/** Voice note player: the waveform is decorative (derived from the id); time
 *  and progress come from the real audio element streaming from the API. */
export function AudioPlayer({ a, large }: { a: Attachment; large?: boolean }) {
  const { t: tr } = useI18n();
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(a.duration_seconds ?? 0);
  const shape = bars(a.id);
  const prog = dur ? t / dur : 0;
  const trn = a.transcription;
  return (
    <div onClick={(e) => e.stopPropagation()}>
      <div className="pk-audio" style={large ? { padding: 12, marginTop: 12 } : undefined}>
        <button className="pk-play" aria-label={playing ? tr("pause_voice") : tr("play_voice")} style={large ? { width: 40, height: 40 } : undefined}
          onClick={() => {
            const el = ref.current;
            if (!el) return;
            if (el.paused) el.play().catch(() => setPlaying(false));
            else el.pause();
          }}>
          <SolidIcon d={playing ? I.pause : I.play} />
        </button>
        <div className="pk-wave">
          {shape.map((h, i) => <span key={i} className={i / shape.length < prog ? "on" : ""} style={{ height: h }} />)}
        </div>
        <span className="pk-time">{playing || t ? `${mmss(t)} / ${mmss(dur)}` : mmss(dur)}</span>
        <audio ref={ref} src={a.content_url} preload="none"
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onEnded={() => { setPlaying(false); setT(0); }}
          onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => Number.isFinite(e.currentTarget.duration) && setDur(e.currentTarget.duration)} />
      </div>
      {trn && (
        <div className="pk-transcript" style={large ? { fontSize: 15, marginTop: 8 } : undefined}>
          <span className="pk-transcript-label">
            {tr("transcript")} · {trn.language ? LANGS[trn.language] ?? trn.language : tr("auto_detected")}
            {large ? " · " + tr("original_kept") : ""}
          </span>
          {trn.text ? <div>{trn.text}</div> : (
            <div className="pk-muted">
              {trn.status === "failed" ? tr("transcription_failed") : tr("transcribing")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Attachments of a thought, as the design lays them out. */
export function ThoughtMedia({ attachments, text, large }: { attachments: Attachment[]; text: string; large?: boolean }) {
  const images = attachments.filter((a) => a.kind === "image");
  const audio = attachments.filter((a) => a.kind === "audio");
  const video = attachments.filter((a) => a.kind === "video");
  const files = attachments.filter((a) => a.kind === "document" || a.kind === "other");
  const link = firstLink(text);
  const n = images.length;
  return (
    <>
      {n > 0 && (
        <div className={"pk-images" + (n > 1 ? " multi" : "") + (n === 3 ? " three" : "")} style={large ? { marginTop: 12 } : undefined}>
          {images.map((a) => (
            <a key={a.id} className="pk-image" href={a.content_url} target="_blank" rel="noopener" onClick={(e) => e.stopPropagation()}>
              {a.processing_status === "failed" ? (
                <span className="pk-image-label">{a.original_filename}</span>
              ) : (
                <img className="grayscale" src={a.thumbnail_url ?? a.content_url} alt={a.original_filename} loading="lazy" />
              )}
            </a>
          ))}
        </div>
      )}
      {audio.map((a) => <AudioPlayer key={a.id} a={a} large={large} />)}
      {video.map((a) => (
        <video key={a.id} className="pk-video" controls preload="metadata" src={a.content_url}
          poster={a.thumbnail_url ?? undefined} onClick={(e) => e.stopPropagation()} />
      ))}
      {link && (
        <a className="pk-linkcard" href={link.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
          <span style={{ fontSize: 12, color: "var(--color-neutral-600)" }}>{link.domain}</span>
          <span style={{ fontWeight: 800, fontSize: 15 }}>{link.title}</span>
        </a>
      )}
      {files.map((a) => (
        <a key={a.id} className="pk-filecard" href={a.content_url} target="_blank" rel="noopener" onClick={(e) => e.stopPropagation()}>
          <strong>{a.original_filename}</strong>
          <span className="pk-muted">{fileSize(a.file_size)}</span>
        </a>
      ))}
    </>
  );
}

/** Scroll the element into view when it first mounts (focused thread part). */
export function useScrollIntoView<T extends HTMLElement>(on: boolean) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (on) ref.current?.scrollIntoView({ block: "nearest" });
  }, [on]);
  return ref;
}
