import { useEffect, useState } from "react";
import { errorMessage, get, post } from "../api/client";
import { useI18n } from "../i18n";
import { I, Icon } from "../lib/icons";
import { mediaClock, SaveQuoteButton } from "../components/MediaQuote";

export const mediaSearchText = {
  ru: { tab: "Общий поиск", subtitle: "Кино, YouTube и песни — в одном поиске", placeholder: "Слово, фраза или смысл — например, страх перед будущим", search: "Найти", keyword: "По словам", semantic: "По смыслу", hybrid: "Слова и смысл", all: "Все типы", movie: "Кино", video: "Видео", music: "Музыка", lyrics: "Тексты песен", unknown: "Тип не определён", languages: "Все языки", genre: "Все жанры", year: "Все годы", loading: "Ищем…", empty: "Ничего не найдено. Попробуйте изменить запрос или фильтры.", hint: "Ищите по названиям, субтитрам и текстам песен. Поиск по смыслу находит близкие фрагменты без буквального совпадения слов.", found: "Найдено фрагментов:", open: "Открыть источник", lines: "Реплики и таймкоды", previous: "Назад", next: "Дальше", update: "Обновить индекс", ready: "Индекс готов", indexing: "Индексируется", syncing: "Собираем медиатексты", absent: "Индекс ещё не создан", failed: "Обновление остановилось. Можно повторить.", progress: "По смыслу доступны", fragments: "фрагментов", local: "Обработка локально на selenium", unavailable: "Поиск по смыслу временно недоступен; показаны совпадения по словам.", unknownLanguage: "Язык не определён", metadata: "Описание", subtitlePart: "Субтитры" },
  en: { tab: "Unified search", subtitle: "Films, YouTube and lyrics in one search", placeholder: "A word, phrase or idea — for example, fear of the future", search: "Search", keyword: "Keywords", semantic: "Meaning", hybrid: "Words and meaning", all: "All types", movie: "Films", video: "Videos", music: "Music", lyrics: "Lyrics", unknown: "Unknown type", languages: "All languages", genre: "All genres", year: "All years", loading: "Searching…", empty: "No matches. Try a different query or filters.", hint: "Search titles, captions and lyrics. Semantic search finds related passages without requiring the same words.", found: "Matching passages:", open: "Open source", lines: "Lines and timestamps", previous: "Previous", next: "Next", update: "Refresh index", ready: "Index ready", indexing: "Indexing", syncing: "Collecting media text", absent: "No index yet", failed: "Index update stopped. You can retry.", progress: "Semantic search covers", fragments: "passages", local: "Processed locally on selenium", unavailable: "Semantic search is unavailable; keyword matches are shown.", unknownLanguage: "Unknown language", metadata: "Description", subtitlePart: "Subtitles" },
  tk: { tab: "Umumy gözleg", subtitle: "Kino, YouTube we aýdymlar bir gözlegde", placeholder: "Söz, sözlem ýa-da pikir — mysal üçin, geljekden gorky", search: "Gözle", keyword: "Söz boýunça", semantic: "Manysy boýunça", hybrid: "Söz we many", all: "Ähli görnüşler", movie: "Kino", video: "Wideo", music: "Saz", lyrics: "Aýdym sözleri", unknown: "Näbelli görnüş", languages: "Ähli diller", genre: "Ähli žanrlar", year: "Ähli ýyllar", loading: "Gözlenýär…", empty: "Netije ýok. Gözlegi ýa-da süzgüçleri üýtgediň.", hint: "Atlar, subtitrler we aýdym sözleri boýunça gözläň. Many boýunça gözleg meňzeş bölekleri tapýar.", found: "Tapylan bölekler:", open: "Çeşmäni aç", lines: "Sözler we wagtlar", previous: "Öňki", next: "Indiki", update: "Indeksi täzele", ready: "Indeks taýýar", indexing: "Indekslenýär", syncing: "Media ýazgylary ýygnalýar", absent: "Indeks heniz döredilmedi", failed: "Täzeleniş saklandy. Täzeden synanyşyň.", progress: "Many gözleginde", fragments: "bölek", local: "Selenium-da ýerli işlenýär", unavailable: "Many boýunça gözleg elýeterli däl; söz boýunça netijeler görkezilýär.", unknownLanguage: "Dil näbelli", metadata: "Düşündiriş", subtitlePart: "Subtitrler" },
};
type Hit = { id: string; title: string; title_en: string | null; kind: "movie" | "video" | "music" | "unknown"; part: string; creator: string | null; year: number | null; language: string; text: string; start_ms: number | null; url: string; poster: string | null; segments: { text: string; start_ms: number | null }[] };
type Page = { items: Hit[]; total: number; semantic_status: string };
type Status = { entries: number; passages: number; embedded: number; phase: string; semantic_enabled: boolean; processed_items: number; total_items: number };
type Facets = { years: number[]; genres: string[]; languages: string[] };

function Result({ hit }: { hit: Hit }) {
  const { lang } = useI18n(); const c = mediaSearchText[lang];
  const [failed, setFailed] = useState(false);
  return <article className="pk-media-result">
    <div className="pk-media-result-head">
      {hit.poster && !failed && <img src={hit.poster} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />}
      <div><div className="pk-archive-tags"><span className="tag tag-accent">{hit.part === "lyrics" ? c.lyrics : c[hit.kind]}</span>{hit.part !== "lyrics" && <span className="tag tag-neutral">{hit.part === "metadata" ? c.metadata : c.subtitlePart}</span>}<span className="tag tag-neutral">{hit.language.toUpperCase()}</span>{hit.year && <span className="tag tag-neutral">{hit.year}</span>}</div>
        <h2>{lang === "en" ? hit.title_en || hit.title : hit.title}</h2>
        {hit.title_en && hit.title_en !== hit.title && <p className="pk-cinema-original-title">{hit.title_en}</p>}
        {hit.creator && <small>{hit.creator}</small>}
      </div>
    </div>
    <p className="pk-media-result-text">{hit.text}</p>
    <div className="pk-cinema-actions"><a className="btn btn-outline" href={hit.url} target={hit.url.startsWith("https:") ? "_blank" : undefined} rel="noreferrer">{mediaClock(hit.start_ms)} {c.open}</a><SaveQuoteButton source="passage" reference={hit.id} /></div>
    {!!hit.segments.length && <details className="pk-media-lines"><summary>{c.lines} · {hit.segments.length}</summary><ol>
      {hit.segments.map((line, position) => <li key={position}><time>{mediaClock(line.start_ms)}</time><span>{line.text}</span><SaveQuoteButton source="passage" reference={hit.id} position={position} /></li>)}
    </ol></details>}
  </article>;
}

export function MediaSearchView() {
  const { lang } = useI18n(); const c = mediaSearchText[lang];
  const [input, setInput] = useState(""); const [query, setQuery] = useState("");
  const [mode, setMode] = useState("keyword"); const [kind, setKind] = useState("");
  const [language, setLanguage] = useState(""); const [genre, setGenre] = useState(""); const [year, setYear] = useState("");
  const [offset, setOffset] = useState(0); const [page, setPage] = useState<Page>();
  const [status, setStatus] = useState<Status>(); const [facets, setFacets] = useState<Facets>();
  const [error, setError] = useState(""); const [revision, setRevision] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    const load = () => { void get<Status>("/media-search/status", undefined, abort.signal).then(data => { if (!abort.signal.aborted) setStatus(data); }, () => {}); };
    load(); const timer = window.setInterval(load, 5000);
    void get<Facets>("/media-search/facets", undefined, abort.signal).then(setFacets, () => {});
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [revision]);
  useEffect(() => {
    if (!query) { setPage(undefined); return; }
    const abort = new AbortController(); setPage(undefined); setError("");
    post<Page>("/media-search", { query, mode, kind, language, genre, year: year ? Number(year) : null, offset, limit: 20 })
      .then(data => { if (!abort.signal.aborted) setPage(data); }, e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    return () => abort.abort();
  }, [query, mode, kind, language, genre, year, offset, revision]);
  async function refresh() {
    setRefreshing(true); setError("");
    try { await post("/media-search/refresh"); setRevision(n => n + 1); }
    catch (e) { setError(errorMessage(e)); }
    finally { setRefreshing(false); }
  }
  return <section className="pk-media-search">
    <div className="pk-pad pk-ruled pk-cinema-toolbar">
      <form className="pk-search-form" onSubmit={e => { e.preventDefault(); setQuery(input.trim()); setOffset(0); }}><input type="search" required maxLength={500} value={input} onChange={e => setInput(e.target.value)} aria-label={c.placeholder} placeholder={c.placeholder} /><button className="btn btn-primary"><Icon d={I.search} size="sm" />{c.search}</button></form>
      <div className="pk-cinema-actions"><div className="pk-seg">{["keyword", "semantic", "hybrid"].map(value => <button key={value} aria-pressed={mode === value} onClick={() => { setMode(value); setOffset(0); }}>{c[value as "keyword" | "semantic" | "hybrid"]}</button>)}</div></div>
      <div className="pk-media-filters">
        <select aria-label={c.all} value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}><option value="">{c.all}</option>{["movie", "video", "music", "lyrics", "unknown"].map(value => <option key={value} value={value}>{c[value as "movie" | "video" | "music" | "lyrics" | "unknown"]}</option>)}</select>
        <select aria-label={c.languages} value={language} onChange={e => { setLanguage(e.target.value); setOffset(0); }}><option value="">{c.languages}</option>{(facets?.languages || ["ru", "en", "tk"]).map(value => <option key={value} value={value}>{({ru:"Русский", en:"English", tk:"Türkmen", und:c.unknownLanguage} as Record<string,string>)[value] || value.toUpperCase()}</option>)}</select>
        <select aria-label={c.genre} value={genre} onChange={e => { setGenre(e.target.value); setOffset(0); }}><option value="">{c.genre}</option>{facets?.genres.map(value => <option key={value} value={value}>{value}</option>)}</select>
        <select aria-label={c.year} value={year} onChange={e => { setYear(e.target.value); setOffset(0); }}><option value="">{c.year}</option>{facets?.years.map(value => <option key={value} value={value}>{value}</option>)}</select>
      </div>
      <div className="pk-media-index"><div><strong>{status?.phase === "ready" ? c.ready : status?.phase === "syncing" ? c.syncing : status?.phase === "indexing" ? c.indexing : status?.phase === "error" ? c.failed : c.absent}</strong>
        {status && <small>{c.progress} {status.embedded} / {status.passages} {c.fragments} · {c.local}{status.phase === "syncing" ? ` · ${status.processed_items}/${status.total_items}` : ""}</small>}</div>
        <button className="btn btn-outline" disabled={refreshing || status?.phase === "syncing" || status?.phase === "indexing"} onClick={() => void refresh()}>{c.update}</button></div>
    </div>
    {!query && <p className="pk-pad pk-muted">{c.hint}</p>}
    {error && <p className="pk-pad pk-cinema-error" role="alert">{error}</p>}
    {query && !page && !error && <p role="status" className="pk-pad">{c.loading}</p>}
    {page && <><p className="pk-pad pk-cinema-count">{c.found} {page.total}</p>{page.semantic_status === "unavailable" && <p className="pk-pad" role="status">{c.unavailable}</p>}
      {page.items.map(hit => <Result key={hit.id} hit={hit} />)}
      {page.total === 0 && <p className="pk-pad">{c.empty}</p>}
      {!!page.total && <div className="pk-archive-tools pk-archive-pagination"><button className="pk-square-btn" aria-label={c.previous} disabled={!offset} onClick={() => setOffset(offset - 20)}><Icon d={I.left} size="sm" /></button><span>{offset + 1}–{Math.min(offset + 20,page.total)} / {page.total}</span><button className="pk-square-btn" aria-label={c.next} disabled={offset + 20 >= page.total} onClick={() => setOffset(offset + 20)}><Icon d={I.right} size="sm" /></button></div>}
    </>}
  </section>;
}
