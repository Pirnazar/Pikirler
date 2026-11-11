import { useEffect, useRef, useState } from "react";
import { get } from "../api/client";
import { Header } from "../components/Header";
import { useI18n } from "../i18n";
import { I, Icon } from "../lib/icons";
import { SaveQuoteButton } from "../components/MediaQuote";

type Video = {
  youtube_video_id: string;
  title: string | null;
  channel_title: string | null;
  artist: string | null;
  liked_at: string | null;
  duration_seconds: number | null;
  content_kind: string;
  thumbnail_url: string | null;
  is_available: boolean;
  transcript_status: string | null;
  lyrics_status: string | null;
};
type Track = {
  source: string | null;
  language_code: string | null;
  status: string;
  full_text: string | null;
  lines: { text: string; start_seconds: number | null; position?: number }[];
};
type Detail = { video: { description: string | null }; transcript: Track | null; lyrics: Track | null };
type Page = { configured: boolean; items: Video[]; total: number };
const pageSize = 36;
const filters = [["", "archive_all"], ["video", "archive_video"], ["music", "archive_music"], ["unknown", "archive_unknown"]] as const;
const musicIcon = "M9 18V5l12-2v13 M9 18a3 2 0 1 1-6 0 3 2 0 0 1 6 0 M21 16a3 2 0 1 1-6 0 3 2 0 0 1 6 0";

function youtube(id: string, start?: number) {
  const url = new URL("https://www.youtube.com/watch");
  url.searchParams.set("v", id);
  if (start !== undefined) url.searchParams.set("t", String(Math.max(0, Math.floor(start))));
  return url.toString();
}

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const tail = String(Math.floor(seconds % 60)).padStart(2, "0");
  return minutes < 60 ? minutes + ":" + tail :
    Math.floor(minutes / 60) + ":" + String(minutes % 60).padStart(2, "0") + ":" + tail;
}

function Preview({ video, eager = false }: { video: Video; eager?: boolean }) {
  const { t } = useI18n();
  const [attempt, setAttempt] = useState(0);
  const id = video.youtube_video_id;
  const fallback = /^[\w-]{11}$/.test(id) ? `https://i.ytimg.com/vi/${id}/mqdefault.jpg` : "";
  const original = video.thumbnail_url?.startsWith("https://i.ytimg.com/") ? video.thumbnail_url : "";
  const src = video.is_available && (attempt === 0 ? original || fallback : attempt === 1 && original !== fallback ? fallback : "");
  const kind = video.content_kind === "music" ? "music" : video.content_kind === "video" ? "video" : "unknown";
  const label = kind === "music" ? t("archive_music") : kind === "video" ? t("archive_video") : t("archive_unknown");
  return <span className="pk-archive-preview">
    <span className="pk-archive-placeholder"><Icon d={kind === "music" ? musicIcon : I.image} /></span>
    {src && <img key={src} src={src} alt="" loading={eager ? "eager" : "lazy"} decoding="async"
      referrerPolicy="no-referrer" onError={() => setAttempt(original ? attempt + 1 : 2)} />}
    <span className={`tag pk-archive-kind ${kind === "video" ? "tag-accent" : "tag-neutral"}`}>{label}</span>
    {video.duration_seconds !== null && <span className="tag pk-archive-duration">{clock(video.duration_seconds)}</span>}
  </span>;
}

function TrackView({ track, label, id, source }: { track: Track | null; label: string; id: string; source: "youtube_transcript" | "lyrics" }) {
  const { t } = useI18n();
  if (track?.status !== "ok") return null;
  return <details className="pk-archive-track">
    <summary>{label} <small>{[track.source, track.language_code].filter(Boolean).join(" · ")}</small></summary>
    {track.lines.length ? <ol>
      {track.lines.map((line, index) => <li key={index}>
        {line.start_seconds !== null && <>
          <a href={youtube(id, line.start_seconds)} target="_blank" rel="noreferrer">{clock(line.start_seconds)}</a>{" "}
        </>}
        {line.text}<span className="pk-inline-save"><SaveQuoteButton source={source} reference={id} position={line.position ?? index} /></span>
      </li>)}
    </ol> : <p>{track.full_text || t("archive_empty")}</p>}
  </details>;
}

function VideoDialog({ video, onClose }: { video: Video; onClose: () => void }) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState(false);
  const id = video.youtube_video_id;

  useEffect(() => {
    dialog.current?.showModal();
    const controller = new AbortController();
    get<Detail>("/youtube-archive/" + encodeURIComponent(id), undefined, controller.signal)
      .then(data => { if (!controller.signal.aborted) setDetail(data); },
        () => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [id]);

  return <dialog ref={dialog} className="pk-archive-dialog" aria-labelledby="pk-archive-dialog-title" onClose={onClose}>
    <div className="pk-archive-dialog-head">
      <h2 id="pk-archive-dialog-title" className="dialog-title">{video.title || id}</h2>
      <button className="pk-square-btn" aria-label={t("archive_close")} onClick={() => dialog.current?.close()}>
        <Icon d={I.x} size="sm" />
      </button>
    </div>
    <div className="pk-archive-dialog-body">
      <Preview video={video} eager />
      <p className="pk-archive-meta">{video.artist || video.channel_title}</p>
      {video.is_available ? <a className="btn btn-primary" href={youtube(id)} target="_blank" rel="noreferrer">
        <Icon d={I.play} size="sm" /> {t("archive_open")}
      </a> : <p>{t("archive_unavailable")}</p>}
      {error && <p role="alert">{t("e_generic")}</p>}
      {!error && !detail && <p role="status">{t("loading")}</p>}
      {detail?.video.description && <details className="pk-archive-track">
        <summary>{t("archive_description")}</summary><p className="pk-archive-description">{detail.video.description}</p>
      </details>}
      {detail && <>
        <TrackView track={detail.transcript} id={id} source="youtube_transcript" label={t("archive_captions")} />
        <TrackView track={detail.lyrics} id={id} source="lyrics" label={t("archive_lyrics")} />
      </>}
    </div>
  </dialog>;
}

export function YoutubeArchiveView({ embedded = false }: { embedded?: boolean }) {
  const { t, locale } = useI18n();
  const section = useRef<HTMLElement>(null);
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page>();
  const [selected, setSelected] = useState<Video>();
  const [listError, setListError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setPage(undefined);
    setListError(false);
    get<Page>("/youtube-archive", { q, kind, limit: pageSize, offset }, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= data.total) setOffset(0);
        else setPage(data);
      }, () => { if (!controller.signal.aborted) setListError(true); });
    return () => controller.abort();
  }, [q, kind, offset]);

  function changePage(next: number) {
    setOffset(next);
    section.current?.closest("main")?.scrollTo({ top: 0 });
  }

  return <>
    {!embedded && <Header title={t("nav_media")} sub={t("archive_title")} />}
    <section ref={section} className="pk-archive">
      <div className="pk-pad pk-ruled pk-archive-controls">
        <form className="pk-search-form pk-archive-search" onSubmit={event => {
          event.preventDefault();
          setOffset(0);
          setQ(input.trim());
        }}>
          <input type="search" maxLength={200} value={input} onChange={event => {
            setInput(event.target.value);
            if (!event.target.value) { setQ(""); setOffset(0); }
          }} aria-label={t("archive_query")} placeholder={t("archive_query")} />
          <button className="btn btn-primary"><Icon d={I.search} size="sm" />{t("archive_search")}</button>
        </form>
        <div className="pk-archive-tools pk-archive-filters" role="group" aria-label={t("archive_filter")}>
          <div className="pk-seg">
            {filters.map(([value, label]) => <button key={value} type="button"
              aria-pressed={kind === value} onClick={() => { setKind(value); setOffset(0); }}>
              {t(label)}
            </button>)}
          </div>
          {page?.configured && <span className="pk-archive-count" aria-live="polite">
            {t("archive_count", { n: page.total.toLocaleString(locale) })}
          </span>}
        </div>
      </div>
      {listError && <p className="pk-pad" role="alert">{t("e_generic")}</p>}
      {!page && !listError && <p className="pk-pad" role="status">{t("loading")}</p>}
      {page && !page.configured && <p className="pk-pad">{t("archive_pending")}</p>}
      {page?.configured && page.total === 0 && <p className="pk-pad">{t("archive_empty")}</p>}
      {!!page?.items.length && <div className="pk-grid pk-ruled pk-archive-grid">
        {page.items.map(video => (
          <button key={video.youtube_video_id} type="button" className="pk-cell pk-archive-card"
            aria-haspopup="dialog" onClick={() => setSelected(video)}>
            <Preview video={video} />
            <span className="pk-archive-card-body">
              <span className="pk-archive-title">{video.title || video.youtube_video_id}</span>
              <span className="pk-archive-meta">{[
                video.artist || video.channel_title,
                video.liked_at && new Date(video.liked_at).toLocaleDateString(locale),
              ].filter(Boolean).join(" · ")}</span>
              <span className="pk-archive-tags">
                {video.transcript_status === "ok" && <span className="tag tag-neutral">{t("archive_captions")}</span>}
                {video.lyrics_status === "ok" && <span className="tag tag-neutral">{t("archive_lyrics")}</span>}
                {!video.is_available && <span className="tag tag-neutral">{t("archive_unavailable")}</span>}
              </span>
            </span>
          </button>
        ))}
      </div>}
      {!!page?.total && <div className="pk-archive-tools pk-archive-pagination">
        <button type="button" className="pk-square-btn" disabled={!offset}
          onClick={() => changePage(offset - pageSize)} aria-label={t("back")}><Icon d={I.left} size="sm" /></button>
        <span>{offset + 1}–{offset + page.items.length} / {page.total}</span>
        <button type="button" className="pk-square-btn" disabled={offset + pageSize >= page.total}
          onClick={() => changePage(offset + pageSize)} aria-label={t("archive_next")}><Icon d={I.right} size="sm" /></button>
      </div>}
    </section>
    {selected && <VideoDialog key={selected.youtube_video_id} video={selected} onClose={() => setSelected(undefined)} />}
  </>;
}
