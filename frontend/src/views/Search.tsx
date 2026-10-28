import { useEffect, useRef, useState, type FormEvent } from "react";
import { errorMessage, get, post, type AskResponse, type SearchResponse, type SearchResult } from "../api/client";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";
import { Empty, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { full, rel, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

const SUGGESTED = ["sq_1", "sq_2", "sq_3", "sq_4", "sq_5"] as const;
const MATCH_KEY = { keyword: "match_keyword", fuzzy: "match_fuzzy", semantic: "match_semantic", hybrid: "match_hybrid" } as const;

type Mode = "ai" | "exact";

export function SearchView({ initialQuery }: { initialQuery?: string }) {
  const app = useApp();
  const { t } = useI18n();
  const [q, setQ] = useState(initialQuery ?? "");
  const [mode, setMode] = useState<Mode>("ai");
  const [searched, setSearched] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [semantic, setSemantic] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiErr, setAiErr] = useState("");
  const [err, setErr] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (initialQuery) submit(initialQuery, "ai");
    else setTimeout(() => input.current?.focus(), 60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(query = q, m = mode) {
    query = query.trim();
    if (!query) return;
    const n = ++seq.current;
    setQ(query);
    setSearched(query);
    setResults(null);
    setAnswer(null);
    setErr("");
    setAiErr("");
    const ask = m === "ai" && app.aiAvailable;
    setAiLoading(ask);
    const list = m === "ai"
      ? post<SearchResponse>("/search/hybrid", { query, limit: 20 })
      : get<SearchResponse>("/search", { q: query, limit: 30 });
    list.then(
      (r) => { if (n === seq.current) { setResults(r.results); setSemantic(r.semantic); } },
      (e) => { if (n === seq.current) { setResults([]); setErr(errorMessage(e)); } },
    );
    if (ask) {
      post<AskResponse>("/ai/ask", { question: query, max_sources: 8 }).then(
        (r) => n === seq.current && setAnswer(r),
        (e) => n === seq.current && setAiErr(errorMessage(e)),
      ).finally(() => n === seq.current && setAiLoading(false));
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const clear = () => {
    seq.current++;
    setQ("");
    setSearched("");
    setResults(null);
    setAnswer(null);
    setAiErr("");
    input.current?.focus();
  };

  return (
    <>
      <Header title={t("nav_search")} sub={t("search_sub")} />
      <div className="pk-pad pk-ruled">
        <form className="pk-search-form" onSubmit={onSubmit}>
          <span style={{ margin: "15px 0 0 14px" }}><Icon d={I.spark} className="accent-stroke" /></span>
          <input ref={input} type="search" aria-label={t("search_placeholder")} placeholder={t("search_placeholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-primary" type="submit">{t("search")}</button>
        </form>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, flexWrap: "wrap" }}>
          <div role="group" aria-label={t("search_mode")} className="pk-seg">
            {([["ai", "ask_ai"], ["exact", "exact_words"]] as const).map(([k, l]) => (
              <button key={k} aria-pressed={mode === k} style={{ padding: "5px 12px", fontSize: 13 }}
                onClick={() => { setMode(k); if (searched) submit(searched, k); }}>{t(l)}</button>
            ))}
          </div>
          <span className="pk-muted" style={{ fontSize: 12 }}>{t("own_only")}</span>
          {searched && (
            <button onClick={clear} style={{ marginLeft: "auto", border: 0, background: "none", cursor: "pointer", fontSize: 13, textDecoration: "underline", textUnderlineOffset: 3 }}>{t("clear")}</button>
          )}
        </div>
        {mode === "ai" && !app.aiAvailable && (
          <div role="status" style={{ marginTop: 12, fontSize: 13, color: "var(--color-neutral-700)" }}>
            {semantic === "available" ? t("ai_off_meaning") : t("ai_off_words")}
            {" "}<button className="btn-ghost btn" style={{ padding: 0, fontSize: 13 }} onClick={() => app.go("settings")}>{t("nav_settings")}</button>
          </div>
        )}
      </div>

      {!searched && (
        <div style={{ padding: "16px 20px 30px" }}>
          <div className="pk-section-label" style={{ marginBottom: 6 }}>{t("try_asking")}</div>
          {SUGGESTED.map((k) => (
            <button key={k} className="pk-suggest" onClick={() => submit(t(k), "ai")}>
              <span style={{ flex: 1 }}>{t(k)}</span><Icon d={I.arrow} size="sm" />
            </button>
          ))}
        </div>
      )}

      {searched && mode === "ai" && app.aiAvailable && (
        <div className="pk-answer">
          <div className="pk-insights-title">{t("ai_answer")} <span>· {t("ai_interpretation")}</span></div>
          {aiLoading && <div style={{ marginTop: 12 }}><div className="pk-skel-line" style={{ width: "90%" }} /><div className="pk-skel-line" style={{ width: "70%" }} /></div>}
          {answer && (
            <>
              <p>{answer.insufficient_data || !answer.answer ? t("ai_insufficient") : answer.answer}</p>
              {answer.sources.length > 0 && (
                <div className="pk-srcs">
                  {answer.sources.map((s, i) => (
                    <button key={s.thought_id} className="pk-src" onClick={() => app.go("detail", s.thought_id)}>
                      <strong>{i + 1}</strong><span>{short(s.created_at)} · {snip(s.snippet, 44)}</span>
                    </button>
                  ))}
                </div>
              )}
              {answer.llm_called && <div className="pk-ai-foot">{t("generated_by", { model: `${answer.provider} · ${answer.model}` })}</div>}
            </>
          )}
          {aiErr && <p role="alert" className="pk-error" style={{ fontWeight: 400 }}>{aiErr}</p>}
        </div>
      )}

      {searched && results === null && <Skeletons />}
      {searched && results && (
        <>
          {err && <div className="pk-pad pk-error" role="alert">{err}</div>}
          {results.length > 0 && <div className="pk-list-heading">{t("original_thoughts", { n: results.length })}</div>}
          {results.map((r) => <ResultRow key={r.thought_id} r={r} />)}
          {!results.length && !err && (
            <Empty title={t("no_thoughts_found")} body={mode === "exact" ? t("exact_empty") : t("ai_empty")} />
          )}
        </>
      )}
    </>
  );
}

function ResultRow({ r }: { r: SearchResult }) {
  const app = useApp();
  const { t } = useI18n();
  return (
    <article className="pk-card" style={{ paddingBottom: 14 }} onClick={() => app.go("detail", r.thought_id)}>
      <div className="pk-avatar">{app.initial}</div>
      <div style={{ minWidth: 0 }}>
        <div className="pk-card-head">
          <strong>{app.name}</strong>
          <span className="pk-muted" title={full(r.created_at)}>· {rel(r.created_at)}</span>
          <span className="pk-muted" style={{ marginLeft: "auto", fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase" }}>
            {r.match_type in MATCH_KEY ? t(MATCH_KEY[r.match_type as keyof typeof MATCH_KEY]) : r.match_type}
          </span>
        </div>
        <div className="pk-text">{r.snippet}</div>
        {r.tags.length > 0 && (
          <div className="pk-meta">
            {r.tags.map((g) => (
              <button key={g} className="tag tag-neutral pk-tag-btn" onClick={(e) => { e.stopPropagation(); app.go("topics", g); }}>#{g}</button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
