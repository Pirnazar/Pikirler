import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, get, patch, post, request } from "../api/client";
import { useApp } from "../app/context";
import { SaveQuoteButton } from "../components/MediaQuote";
import { useI18n } from "../i18n";
import { I, Icon } from "../lib/icons";

export const cinemaText = {
  ru: {
    cinema: "Кино", subtitle: "Фильмы и поиск по репликам", add: "Добавить фильм", catalog: "Поиск фильма",
    titleQuery: "Название фильма — например, Интерстеллар", search: "Найти", choose: "Выберите фильм и год выпуска",
    library: "Мои фильмы", quotes: "Поиск по репликам", quoteQuery: "Слово или фраза из фильма",
    empty: "Добавьте первый фильм. Его субтитры станут частью вашей поисковой библиотеки.",
    noResults: "Ничего не найдено", loading: "Загрузка…", adding: "Добавляю…", close: "Закрыть",
    pending: "Ищем субтитры…", ready: "Субтитры сохранены", none: "Субтитры не найдены",
    failed: "Провайдер субтитров недоступен. Можно повторить поиск или загрузить свой файл.",
    retry: "Найти субтитры", upload: "Загрузить SRT / VTT", language: "Язык субтитров",
    track: "Дорожка субтитров", findInside: "Найти в этом фильме", cueHint: "Время соответствует выбранной версии субтитров.",
    provider: "Другие версии", getTrack: "Сохранить дорожку", fileLimit: "Нужен SRT или VTT размером до 5 МБ.",
    source: "Источник", uploaded: "Свой файл", count: "Фильмов: ", quoteCount: "Найдено реплик: ",
    previous: "Предыдущая страница", next: "Следующая страница", director: "Режиссёр", cast: "В ролях",
    saved: "Сохранён", quotesHint: "Поиск работает по сохранённым субтитрам, включая разные формы русских и английских слов.",
    catalogError: "Каталог временно недоступен. Попробуйте ещё раз.",
    englishData: "Данные на английском", refresh: "Обновить данные и субтитры",
    localizing: "Загружаем русские данные…", localizationFailed: "Русские данные пока не загрузились. Повторите обновление.",
    partial: "Сохранена часть языков", allLanguages: "Все языки", missing: "не найдены", waiting: "в очереди", unavailable: "ошибка источника",
    allFilms: "Все фильмы", want: "Хочу посмотреть", watched: "Просмотрено", favorite: "Избранное", unlisted: "Без списка", personal: "Моя коллекция", rating: "Моя оценка", noRating: "Без оценки", comment: "Мой комментарий", savePreferences: "Сохранить", preferencesSaved: "Сохранено",
  },
  en: {
    cinema: "Cinema", subtitle: "Films and searchable dialogue", add: "Add a film", catalog: "Find a film",
    titleQuery: "Film title — for example, Interstellar", search: "Search", choose: "Choose the film and release year",
    library: "My films", quotes: "Search dialogue", quoteQuery: "A word or phrase from a film",
    empty: "Add your first film. Its subtitles will become part of your searchable library.",
    noResults: "No results", loading: "Loading…", adding: "Adding…", close: "Close",
    pending: "Looking for subtitles…", ready: "Subtitles saved", none: "No subtitles found",
    failed: "The subtitle provider is unavailable. Retry or upload your own file.",
    retry: "Find subtitles", upload: "Upload SRT / VTT", language: "Subtitle language",
    track: "Subtitle track", findInside: "Search this film", cueHint: "Timestamps follow the selected subtitle version.",
    provider: "Other versions", getTrack: "Save track", fileLimit: "Choose an SRT or VTT file up to 5 MB.",
    source: "Source", uploaded: "Uploaded file", count: "Films: ", quoteCount: "Matching lines: ",
    previous: "Previous page", next: "Next page", director: "Director", cast: "Cast",
    saved: "Saved", quotesHint: "Search saved subtitles, including different Russian and English word forms.",
    catalogError: "The catalog is temporarily unavailable. Please try again.",
    englishData: "English metadata", refresh: "Update metadata and subtitles",
    localizing: "Loading Russian metadata…", localizationFailed: "Russian metadata is unavailable. Retry the update.",
    partial: "Some languages saved", allLanguages: "All languages", missing: "not found", waiting: "queued", unavailable: "provider error",
    allFilms: "All films", want: "Watchlist", watched: "Watched", favorite: "Favorites", unlisted: "No list", personal: "My collection", rating: "My rating", noRating: "No rating", comment: "My comment", savePreferences: "Save", preferencesSaved: "Saved",
  },
  tk: {
    cinema: "Kino", subtitle: "Filmler we sözler boýunça gözleg", add: "Film goş", catalog: "Film gözle",
    titleQuery: "Filmiň ady — mysal üçin, Interstellar", search: "Gözle", choose: "Filmi we çykan ýylyny saýlaň",
    library: "Filmlerim", quotes: "Sözler boýunça gözleg", quoteQuery: "Filmdäki söz ýa-da sözlem",
    empty: "Ilkinji filmi goşuň. Onuň subtitrleri şahsy gözleg kitaphanaňyza goşular.",
    noResults: "Netije tapylmady", loading: "Ýüklenýär…", adding: "Goşulýar…", close: "Ýap",
    pending: "Subtitrler gözlenýär…", ready: "Subtitrler saklandy", none: "Subtitrler tapylmady",
    failed: "Subtitr hyzmaty elýeterli däl. Täzeden synanyşyň ýa-da öz faýlyňyzy ýükläň.",
    retry: "Subtitr gözle", upload: "SRT / VTT ýükle", language: "Subtitr dili",
    track: "Subtitr görnüşi", findInside: "Şu filmde gözle", cueHint: "Wagt bellikleri saýlanan subtitr görnüşine degişlidir.",
    provider: "Başga görnüşler", getTrack: "Subtitri sakla", fileLimit: "5 MB çenli SRT ýa-da VTT faýly saýlaň.",
    source: "Çeşme", uploaded: "Öz faýlym", count: "Filmler: ", quoteCount: "Tapylan sözlemler: ",
    previous: "Öňki sahypa", next: "Indiki sahypa", director: "Režissýor", cast: "Rollarda",
    saved: "Saklandy", quotesHint: "Saklanan subtitrlerde sözleri we sözlemleri gözläň.",
    catalogError: "Film katalogy wagtlaýyn elýeterli däl. Täzeden synanyşyň.",
    englishData: "Iňlis dilindäki maglumatlar", refresh: "Maglumatlary we subtitrleri täzele",
    localizing: "Rus dilindäki maglumatlar ýüklenýär…", localizationFailed: "Rus dilindäki maglumatlar elýeterli däl. Täzeden synanyşyň.",
    partial: "Käbir diller saklandy", allLanguages: "Ähli diller", missing: "tapylmady", waiting: "nobatda", unavailable: "çeşme ýalňyşlygy",
    allFilms: "Ähli filmler", want: "Görmek isleýän", watched: "Görülen", favorite: "Halanlarym", unlisted: "Sanawsyz", personal: "Toplumym", rating: "Meniň baham", noRating: "Baha ýok", comment: "Meniň bellligim", savePreferences: "Sakla", preferencesSaved: "Saklandy",
  },
};

type Movie = {
  id: string; imdb_id: string; title: string; year: string | null; poster: string | null;
  description: string | null; runtime: string | null; rating: string | null;
  genres: string[]; director: string[]; cast: string[];
  subtitle_status: string; subtitle_error: string | null; subtitle_count?: number;
  localized?: Record<string, Partial<Movie> & { source?: string; source_url?: string }>;
  metadata_status?: string; subtitle_languages?: Record<string, string>;
  watch_status: string; is_favorite: boolean; user_rating: number | null; user_comment: string; preferences_version: number;
};
type Track = { id: string; language: string; source: string; filename: string; cue_count: number };
type Detail = { movie: Movie; tracks: Track[] };
type Cue = { id: number; text: string; start_ms: number; end_ms: number; movie_id: string; title: string; title_ru?: string; title_en?: string; year: string | null; language: string; source: string; subtitle_id: string };
type Page<T> = { items: T[]; total: number };
type Choice = { id: string; language: string; label: string };

function localizedMovie(movie: Movie, lang: string): Movie {
  const data = movie.localized?.[lang === "ru" ? "ru" : "en"];
  if (!data) return movie;
  return { ...movie, ...Object.fromEntries(Object.entries(data).filter(([, value]) => value != null)) } as Movie;
}
function SubtitleLanguages({ movie }: { movie: Movie }) {
  const { lang } = useI18n(); const c = cinemaText[lang];
  return <span className="pk-cinema-language-badges">{["ru", "en"].map(language => {
    const state = movie.subtitle_languages?.[language];
    const label = state === "ready" ? "✓" : state === "pending" ? c.waiting : state === "failed" ? c.unavailable : c.missing;
    return <span className={`tag tag-neutral ${state === "ready" ? "is-ready" : ""}`} key={language}>{language.toUpperCase()} · {label}</span>;
  })}</span>;
}

function clock(ms: number) {
  const seconds = Math.floor(ms / 1000);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(x => String(x).padStart(2, "0")).join(":");
}
function Poster({ movie }: { movie: Pick<Movie, "poster" | "title"> }) {
  const [failed, setFailed] = useState(false);
  return <span className="pk-cinema-poster">
    {movie.poster && !failed ? <img src={movie.poster} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} /> : <Icon d={I.video} />}
  </span>;
}
function Pagination({ offset, limit, total, change }: { offset: number; limit: number; total: number; change: (n: number) => void }) {
  const { lang } = useI18n(); const c = cinemaText[lang];
  if (!total) return null;
  return <div className="pk-archive-tools pk-archive-pagination">
    <button className="pk-square-btn" disabled={offset === 0} onClick={() => change(offset - limit)} aria-label={c.previous}><Icon d={I.left} size="sm" /></button>
    <span>{offset + 1}–{Math.min(total, offset + limit)} / {total}</span>
    <button className="pk-square-btn" disabled={offset + limit >= total} onClick={() => change(offset + limit)} aria-label={c.next}><Icon d={I.right} size="sm" /></button>
  </div>;
}

function MoviePreferences({ movie, onSaved }: { movie: Movie; onSaved: () => Promise<void> }) {
  const app = useApp();
  const { lang } = useI18n(); const c = cinemaText[lang];
  const [status, setStatus] = useState(movie.watch_status);
  const [favorite, setFavorite] = useState(movie.is_favorite);
  const [rating, setRating] = useState(movie.user_rating?.toString() || "");
  const [comment, setComment] = useState(movie.user_comment);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [saved, setSaved] = useState(false);
  async function save() {
    setBusy(true); setError(""); setSaved(false);
    try {
      await patch(`/movies/${movie.id}/preferences`, { expected_version: movie.preferences_version, watch_status: status, is_favorite: favorite, user_rating: rating ? Number(rating) : null, user_comment: comment });
      await onSaved(); setSaved(true); app.toast(c.preferencesSaved);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <details className="pk-movie-personal"><summary>{c.personal}{movie.watch_status === "want" ? ` · ${c.want}` : movie.watch_status === "watched" ? ` · ${c.watched}` : ""}{movie.user_rating ? ` · ★ ${movie.user_rating}/10` : ""}</summary><form onSubmit={e => { e.preventDefault(); void save(); }}>
    <div className="pk-cinema-actions">
      <select aria-label={c.personal} value={status} onChange={e => setStatus(e.target.value)}><option value="unlisted">{c.unlisted}</option><option value="want">{c.want}</option><option value="watched">{c.watched}</option></select>
      <label className="pk-movie-favorite"><input type="checkbox" checked={favorite} onChange={e => setFavorite(e.target.checked)} />{c.favorite}</label>
      <select aria-label={c.rating} value={rating} onChange={e => setRating(e.target.value)}><option value="">{c.noRating}</option>{Array.from({length:10}, (_,i) => <option key={i+1} value={i+1}>{i+1} / 10</option>)}</select>
    </div><label className="pk-movie-comment">{c.comment}<textarea maxLength={10000} rows={3} value={comment} onChange={e => setComment(e.target.value)} /></label>
    <div className="pk-cinema-actions"><button className="btn btn-primary" disabled={busy}>{c.savePreferences}</button>{saved && <span role="status">{c.preferencesSaved}</span>}</div>
    {error && <p role="alert" className="pk-cinema-error">{error}</p>}
  </form></details>;
}

function AddFilm({ onClose, onAdded }: { onClose: () => void; onAdded: (id: string) => void }) {
  const { lang } = useI18n(); const c = cinemaText[lang];
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Movie[]>();
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<string>();
  const [error, setError] = useState("");
  const controller = useRef<AbortController>();
  useEffect(() => { dialog.current?.showModal(); input.current?.focus(); return () => controller.current?.abort(); }, []);
  async function search() {
    if (query.trim().length < 2) return;
    controller.current?.abort(); const next = new AbortController(); controller.current = next;
    setBusy(true); setError(""); setResults(undefined);
    try { const data = await get<{ items: Movie[] }>("/movies/catalog", { q: query.trim() }, next.signal); if (!next.signal.aborted) setResults(data.items); }
    catch { if (!next.signal.aborted) setError(c.catalogError); }
    finally { if (!next.signal.aborted) setBusy(false); }
  }
  async function add(movie: Movie) {
    setAdding(movie.imdb_id); setError("");
    try { const saved = await post<Movie>("/movies", { imdb_id: movie.imdb_id, query: query.trim() }); onAdded(saved.id); }
    catch (e) { setError(errorMessage(e)); setAdding(undefined); }
  }
  return <dialog ref={dialog} className="pk-archive-dialog pk-cinema-add" onClose={onClose} aria-modal="true" aria-labelledby="cinema-add-title">
    <div className="pk-archive-dialog-head"><h2 id="cinema-add-title" className="dialog-title">{c.catalog}</h2>
      <button className="pk-square-btn" aria-label={c.close} onClick={() => dialog.current?.close()}><Icon d={I.x} size="sm" /></button></div>
    <div className="pk-archive-dialog-body">
      <form className="pk-search-form" onSubmit={e => { e.preventDefault(); void search(); }}>
        <input ref={input} type="search" required minLength={2} maxLength={120} value={query} onChange={e => setQuery(e.target.value)} placeholder={c.titleQuery} aria-label={c.titleQuery} />
        <button className="btn btn-primary" disabled={busy || !!adding}>{c.search}</button>
      </form>
      {error && <p role="alert" className="pk-cinema-error">{error}</p>}
      {busy && <p role="status">{c.loading}</p>}
      {results && <><p className="pk-muted">{results.length ? c.choose : c.noResults}</p>
        <div className="pk-cinema-results">{results.map(movie => <button key={movie.imdb_id} className="pk-cinema-result" disabled={!!adding} onClick={() => void add(movie)}>
          <Poster movie={movie} /><span><strong>{localizedMovie(movie, lang).title}</strong><small>{[movie.year, movie.runtime].filter(Boolean).join(" · ")}</small>
            <span className="pk-cinema-result-action">{adding === movie.imdb_id ? c.adding : c.add}</span></span>
        </button>)}</div></>}
    </div>
  </dialog>;
}

function FilmDialog({ id, initialTrack, initialQuery = "", initialTime, onClose, onChanged }: {
  id: string; initialTrack?: string; initialQuery?: string; initialTime?: number; onClose: () => void; onChanged: () => void;
}) {
  const { lang } = useI18n(); const c = cinemaText[lang];
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<Detail>();
  const [track, setTrack] = useState(initialTrack || "");
  const trackChosen = useRef(!!initialTrack);
  const [language, setLanguage] = useState("ru");
  const [input, setInput] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [offset, setOffset] = useState(0);
  const [cues, setCues] = useState<Page<Cue>>();
  const cueList = useRef<HTMLOListElement>(null);
  const scrolledToSource = useRef(false);
  const focused = useRef(false);
  const [options, setOptions] = useState<Choice[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const data = await get<Detail>("/movies/" + id, undefined, signal);
    if (signal?.aborted) return;
    setDetail(data); setTrack(value => trackChosen.current ? value : (data.tracks.find(item => item.language === "ru") || data.tracks[0])?.id || "");
  }, [id]);
  useEffect(() => {
    dialog.current?.showModal(); const abort = new AbortController();
    void refresh(abort.signal).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    return () => abort.abort();
  }, [refresh]);
  useEffect(() => {
    if (detail?.movie.subtitle_status !== "pending" && detail?.movie.metadata_status !== "pending") return;
    const abort = new AbortController();
    const timer = window.setInterval(() => { void refresh(abort.signal).catch(() => {}); }, 3000);
    return () => { window.clearInterval(timer); abort.abort(); };
  }, [detail?.movie.subtitle_status, detail?.movie.metadata_status, refresh]);
  useEffect(() => {
    if (!track) return;
    const abort = new AbortController(); setCues(undefined);
    get<Page<Cue> & { offset?: number }>(`/movies/${id}/cues`, { subtitle_id: track, q: query, offset, limit: 50, around_ms: !focused.current && !query ? initialTime : undefined }, abort.signal)
      .then(data => { if (!abort.signal.aborted) { focused.current = true; if (data.offset !== undefined && data.offset !== offset) setOffset(data.offset); setCues(data); } }, e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    return () => abort.abort();
  }, [id, track, query, offset, initialTime]);
  useEffect(() => {
    if (initialTime === undefined || scrolledToSource.current) return;
    const target = cueList.current?.querySelector(".is-current");
    if (target) { target.scrollIntoView({block:"center"}); scrolledToSource.current = true; }
  }, [cues, initialTime]);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await refresh(); onChanged(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  const original = detail?.movie;
  const movie = original && localizedMovie(original, lang);
  const status = movie?.subtitle_status;
  return <dialog ref={dialog} className="pk-archive-dialog pk-cinema-dialog" aria-modal="true" aria-labelledby="cinema-film-title" onClose={onClose}>
    <div className="pk-archive-dialog-head"><h2 id="cinema-film-title" className="dialog-title">{movie?.title || c.loading}</h2>
      <button className="pk-square-btn" aria-label={c.close} onClick={() => dialog.current?.close()}><Icon d={I.x} size="sm" /></button></div>
    <div className="pk-archive-dialog-body">
      {movie && detail && <><div className="pk-cinema-overview"><Poster movie={movie} /><div>
        {original && original.title !== movie.title && <p className="pk-cinema-original-title">{original.title}</p>}
        <p className="pk-cinema-meta">{[movie.year, movie.runtime, movie.rating && `IMDb ${movie.rating}`].filter(Boolean).join(" · ")}</p>
        <div className="pk-cinema-genres">{movie.genres.map(genre => <span key={genre} className="tag tag-neutral">{genre}</span>)}</div>
        {movie.description && <p>{movie.description}</p>}
        {!!movie.director.length && <p><strong>{c.director}:</strong> {movie.director.join(", ")}</p>}
        {!!movie.cast.length && <p><strong>{c.cast}:</strong> {movie.cast.slice(0, 10).join(", ")}</p>}
        <small>{c.source}: <a href={`https://www.imdb.com/title/${movie.imdb_id}/`} target="_blank" rel="noreferrer">Cinemeta / IMDb</a>
          {original?.localized?.ru?.source === "tmdb" && <> · <a href={original.localized.ru.source_url || "https://www.themoviedb.org/"} target="_blank" rel="noreferrer">TMDB</a></>}</small>
      </div></div>
      {original?.metadata_status === "pending" && <p role="status" className="pk-muted">{c.localizing}</p>}
      {original?.metadata_status === "failed" && <p className="pk-muted">{c.localizationFailed}</p>}
      {lang === "ru" && original?.localized?.ru && <details className="pk-cinema-english"><summary>{c.englishData} · {original.title}</summary>
        <p>{original.description}</p><p>{original.genres.join(" · ")}</p>
        <p><strong>Director:</strong> {original.director.join(", ")}</p><p><strong>Cast:</strong> {original.cast.join(", ")}</p>
      </details>}
      {original && <MoviePreferences key={`${original.id}:${original.preferences_version}`} movie={original} onSaved={async () => { await refresh(); onChanged(); }} />}
      <div className="pk-cinema-subtitle-tools">
        <p role="status">{status === "pending" ? c.pending : status === "ready" ? c.ready : status === "partial" ? c.partial : status === "failed" ? c.failed : c.none}</p>
        <SubtitleLanguages movie={movie} />
        <div className="pk-cinema-actions">
          <select aria-label={c.language} value={language} onChange={e => { setLanguage(e.target.value); setOptions(undefined); }}>
            <option value="ru">Русский</option><option value="en">English</option><option value="tk">Türkmen</option><option value="tr">Türkçe</option>
          </select>
          <label className={`btn btn-primary pk-cinema-upload ${busy ? "is-busy" : ""}`}>
            {c.upload}<input type="file" accept=".srt,.vtt" disabled={busy} aria-label={c.upload} onChange={e => {
              const file = e.target.files?.[0]; e.target.value = "";
              if (!file) return;
              if (file.size > 5 * 1024 * 1024 || !/\.(srt|vtt)$/i.test(file.name)) { setError(c.fileLimit); return; }
              const form = new FormData(); form.append("file", file); form.append("language", language);
              void action(async () => { const saved = await request<{ id: string }>("POST", `/movies/${id}/subtitles/upload`, { body: form }); trackChosen.current = true; setTrack(saved.id); setOffset(0); setQuery(""); setInput(""); });
            }} />
          </label>
          {status !== "pending" && <button className="btn btn-outline" disabled={busy || movie.metadata_status === "pending"} onClick={() => void action(() => post(`/movies/${id}/refresh`))}>{c.refresh}</button>}
          <button className="btn btn-outline" disabled={busy} onClick={() => void action(async () => {
            const data = await get<{ items: Choice[] }>(`/movies/${id}/subtitles/options`, { language }); setOptions(data.items);
          })}>{c.provider}</button>
        </div>
        {options && <div className="pk-cinema-options">{options.length ? options.map(option => <button className="btn btn-outline" key={option.id} disabled={busy} onClick={() => void action(async () => {
          const saved = await post<{ id: string }>(`/movies/${id}/subtitles/fetch`, { provider_id: option.id, language }); trackChosen.current = true; setTrack(saved.id); setOffset(0); setOptions(undefined);
        })}>{c.getTrack} · {option.label}</button>) : <p>{c.noResults}</p>}</div>}
      </div>
      {!!detail.tracks.length && <div className="pk-cinema-lines">
        <select aria-label={c.track} value={track} onChange={e => { trackChosen.current = true; setTrack(e.target.value); setOffset(0); }}>
          {detail.tracks.map(item => <option key={item.id} value={item.id}>{item.language.toUpperCase()} · {item.source === "upload" ? c.uploaded : "OpenSubtitles"} · {item.filename} ({item.cue_count})</option>)}
        </select>
        <form className="pk-search-form" onSubmit={e => { e.preventDefault(); setQuery(input.trim()); setOffset(0); }}>
          <input type="search" maxLength={200} value={input} aria-label={c.findInside} placeholder={c.findInside} onChange={e => { setInput(e.target.value); if (!e.target.value) { setQuery(""); setOffset(0); } }} />
          <button className="btn btn-primary">{c.search}</button>
        </form>
        <p className="pk-muted">{c.cueHint}</p>
        {!cues ? <p role="status">{c.loading}</p> : <><ol ref={cueList} className="pk-cinema-cues">{cues.items.map(cue => <li key={cue.id} className={initialTime === cue.start_ms ? "is-current" : undefined}><time>{clock(cue.start_ms)}</time><span>{cue.text}<span className="pk-inline-save"><SaveQuoteButton source="cinema" reference={String(cue.id)} /></span></span></li>)}</ol>
          {cues.total === 0 && <p>{c.noResults}</p>}
          <Pagination offset={offset} limit={50} total={cues.total} change={setOffset} /></>}
      </div>}
      </>}
      {error && <p role="alert" className="pk-cinema-error">{error}</p>}
    </div>
  </dialog>;
}

export function MoviesView() {
  const app = useApp();
  const { lang } = useI18n(); const c = cinemaText[lang];
  const [mode, setMode] = useState<"library" | "quotes">("library");
  const [quoteLanguage, setQuoteLanguage] = useState("");
  const [libraryFilter, setLibraryFilter] = useState("all");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<Movie>>();
  const [quotes, setQuotes] = useState<Page<Cue>>();
  const [revision, setRevision] = useState(0);
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<{ id: string; track?: string; query?: string; at?: number }>();
  useEffect(() => {
    const match = app.route.arg?.match(/^movies\/([a-f0-9-]{36})(?:\?(.*))?$/);
    if (match) { const params = new URLSearchParams(match[2] || ""); const at = Number(params.get("at")); setSelected({id:match[1],track:params.get("track") || undefined,at:Number.isFinite(at) && at >= 0 ? at : undefined}); }
  }, [app.route.arg]);
  const [error, setError] = useState("");
  const limit = mode === "library" ? 24 : 30;
  useEffect(() => {
    const abort = new AbortController(); setError("");
    if (mode === "library") {
      setPage(undefined);
      get<Page<Movie>>("/movies", { q: query, offset, limit, watch_status: ["want", "watched"].includes(libraryFilter) ? libraryFilter : "", favorite: libraryFilter === "favorite" }, abort.signal).then(data => { if (!abort.signal.aborted) setPage(data); }, e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    } else {
      setQuotes(undefined);
      if (query) get<Page<Cue>>("/movies/quotes", { q: query, offset, limit, language: quoteLanguage }, abort.signal).then(data => { if (!abort.signal.aborted) setQuotes(data); }, e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    }
    return () => abort.abort();
  }, [mode, query, offset, limit, revision, quoteLanguage, libraryFilter]);
  return <section className="pk-cinema">
    <div className="pk-pad pk-ruled pk-cinema-toolbar">
      <div className="pk-cinema-actions"><div className="pk-seg">
        <button aria-pressed={mode === "library"} onClick={() => { setMode("library"); setQuery(""); setInput(""); setOffset(0); }}>{c.library}</button>
        <button aria-pressed={mode === "quotes"} onClick={() => { setMode("quotes"); setQuery(""); setInput(""); setOffset(0); }}>{c.quotes}</button>
      </div><button className="btn btn-primary" onClick={() => setAdding(true)}><Icon d={I.plus} size="sm" />{c.add}</button></div>
      <form className="pk-search-form" onSubmit={e => { e.preventDefault(); setQuery(input.trim()); setOffset(0); }}>
        <input type="search" maxLength={mode === "library" ? 120 : 200} value={input} aria-label={mode === "library" ? c.library : c.quoteQuery} placeholder={mode === "library" ? c.library : c.quoteQuery}
          onChange={e => { setInput(e.target.value); if (!e.target.value) { setQuery(""); setOffset(0); } }} />
        <button className="btn btn-primary"><Icon d={I.search} size="sm" />{c.search}</button>
      </form>
      {mode === "library" && <div className="pk-seg">{["all","want","watched","favorite"].map(value => <button key={value} aria-pressed={libraryFilter === value} onClick={() => { setLibraryFilter(value); setOffset(0); }}>{value === "all" ? c.allFilms : c[value as "want" | "watched" | "favorite"]}</button>)}</div>}
      {mode === "quotes" && <><select aria-label={c.language} value={quoteLanguage} onChange={e => { setQuoteLanguage(e.target.value); setOffset(0); }}>
        <option value="">{c.allLanguages}</option><option value="ru">Русский</option><option value="en">English</option>
      </select><p className="pk-muted">{c.quotesHint}</p></>}
    </div>
    {error && <p className="pk-pad pk-cinema-error" role="alert">{error}</p>}
    {mode === "library" && <>
      {!page && !error && <p className="pk-pad" role="status">{c.loading}</p>}
      {page?.total === 0 && <div className="pk-pad pk-cinema-empty"><Icon d={I.video} /><p>{query || libraryFilter !== "all" ? c.noResults : c.empty}</p>{!query && libraryFilter === "all" && <button className="btn btn-primary" onClick={() => setAdding(true)}>{c.add}</button>}</div>}
      {!!page?.items.length && <><p className="pk-pad pk-cinema-count">{c.count}{page.total}</p><div className="pk-cinema-grid">
        {page.items.map(movie => <button className="pk-cinema-card" key={movie.id} onClick={() => setSelected({ id: movie.id })} aria-haspopup="dialog">
          <Poster movie={movie} /><span className="pk-cinema-card-body"><strong>{localizedMovie(movie, lang).title}</strong>
            {localizedMovie(movie, lang).title !== movie.title && <small className="pk-cinema-original-title">{movie.title}</small>}
            <span className="pk-cinema-meta">{[movie.year, localizedMovie(movie, lang).runtime].filter(Boolean).join(" · ")}</span>
            <SubtitleLanguages movie={movie} /></span>
            {(movie.is_favorite || movie.user_rating) && <span className="pk-cinema-card-personal">{movie.is_favorite ? "♥ " : ""}{movie.user_rating ? `★ ${movie.user_rating}/10` : ""}</span>}
        </button>)}
      </div><Pagination offset={offset} limit={limit} total={page.total} change={setOffset} /></>}
    </>}
    {mode === "quotes" && query && <>
      {!quotes && !error && <p className="pk-pad" role="status">{c.loading}</p>}
      {quotes && <><p className="pk-pad pk-cinema-count">{c.quoteCount}{quotes.total}</p>
        {quotes.total === 0 && <p className="pk-pad">{c.noResults}</p>}
        <div className="pk-cinema-quote-list">{quotes.items.map(cue => <button key={cue.id} className="pk-cinema-quote" onClick={() => setSelected({ id: cue.movie_id, track: cue.subtitle_id, query })}>
          <time>{clock(cue.start_ms)}</time><span><strong>{(lang === "ru" ? cue.title_ru : cue.title_en) || cue.title} {cue.year && `(${cue.year})`}</strong><span>{cue.text}</span><small>{cue.language.toUpperCase()} · {cue.source === "upload" ? c.uploaded : "OpenSubtitles"}</small></span>
        </button>)}</div><Pagination offset={offset} limit={limit} total={quotes.total} change={setOffset} /></>}
    </>}
    {adding && <AddFilm onClose={() => setAdding(false)} onAdded={id => { setAdding(false); setRevision(n => n + 1); setSelected({ id }); }} />}
    {selected && <FilmDialog key={`${selected.id}:${selected.at || ""}`} id={selected.id} initialTrack={selected.track} initialQuery={selected.query} initialTime={selected.at}
      onClose={() => { setSelected(undefined); setRevision(n => n + 1); }} onChanged={() => setRevision(n => n + 1)} />}
  </section>;
}
