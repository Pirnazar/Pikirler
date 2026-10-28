import { useEffect, useRef, useState, type FormEvent } from "react";
import { errorMessage, get, post, type AskResponse, type SearchResponse, type SearchResult } from "../api/client";
import { useApp } from "../app/context";
import { Empty, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { full, rel, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

const SUGGESTED = [
  "What was I thinking about last summer?",
  "Find my unfinished ideas.",
  "What did I write about my projects last month?",
  "Men iň köp näme barada ýazýaryn?",
  "О чём я думал на прошлой неделе?",
];

const MATCH_LABEL: Record<string, string> = { keyword: "Words", fuzzy: "Similar spelling", semantic: "Meaning", hybrid: "Words + meaning" };

type Mode = "ai" | "exact";

export function SearchView({ initialQuery }: { initialQuery?: string }) {
  const app = useApp();
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
      <Header title="AI Search" sub="Ask your second brain" />
      <div className="pk-pad pk-ruled">
        <form className="pk-search-form" onSubmit={onSubmit}>
          <span style={{ margin: "15px 0 0 14px" }}><Icon d={I.spark} className="accent-stroke" /></span>
          <input ref={input} type="search" aria-label="Ask your thoughts" placeholder="Ask your thoughts anything…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-primary" type="submit">Search</button>
        </form>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, flexWrap: "wrap" }}>
          <div role="group" aria-label="Search mode" className="pk-seg">
            {([["ai", "Ask AI"], ["exact", "Exact words"]] as const).map(([k, l]) => (
              <button key={k} aria-pressed={mode === k} style={{ padding: "5px 12px", fontSize: 13 }}
                onClick={() => { setMode(k); if (searched) submit(searched, k); }}>{l}</button>
            ))}
          </div>
          <span className="pk-muted" style={{ fontSize: 12 }}>Only your own thoughts are searched</span>
          {searched && (
            <button onClick={clear} style={{ marginLeft: "auto", border: 0, background: "none", cursor: "pointer", fontSize: 13, textDecoration: "underline", textUnderlineOffset: 3 }}>Clear</button>
          )}
        </div>
        {mode === "ai" && !app.aiAvailable && (
          <div role="status" style={{ marginTop: 12, fontSize: 13, color: "var(--color-neutral-700)" }}>
            AI answers are off — “Ask AI” searches your thoughts by words{semantic === "available" ? " and meaning" : ""} without asking a model.
            {" "}<button className="btn-ghost btn" style={{ padding: 0, fontSize: 13 }} onClick={() => app.go("settings")}>Settings</button>
          </div>
        )}
      </div>

      {!searched && (
        <div style={{ padding: "16px 20px 30px" }}>
          <div className="pk-section-label" style={{ marginBottom: 6 }}>Try asking</div>
          {SUGGESTED.map((s) => (
            <button key={s} className="pk-suggest" onClick={() => submit(s, "ai")}>
              <span style={{ flex: 1 }}>{s}</span><Icon d={I.arrow} size="sm" />
            </button>
          ))}
        </div>
      )}

      {searched && mode === "ai" && app.aiAvailable && (
        <div className="pk-answer">
          <div className="pk-insights-title">AI answer <span>· an interpretation of your thoughts, not your words</span></div>
          {aiLoading && <div style={{ marginTop: 12 }}><div className="pk-skel-line" style={{ width: "90%" }} /><div className="pk-skel-line" style={{ width: "70%" }} /></div>}
          {answer && (
            <>
              <p>{answer.insufficient_data || !answer.answer ? "I couldn't find enough in your thoughts to answer that." : answer.answer}</p>
              {answer.sources.length > 0 && (
                <div className="pk-srcs">
                  {answer.sources.map((s, i) => (
                    <button key={s.thought_id} className="pk-src" onClick={() => app.go("detail", s.thought_id)}>
                      <strong>{i + 1}</strong><span>{short(s.created_at)} · {snip(s.snippet, 44)}</span>
                    </button>
                  ))}
                </div>
              )}
              {answer.llm_called && <div className="pk-ai-foot">Generated by {answer.provider} · {answer.model}.</div>}
            </>
          )}
          {aiErr && <p role="alert" className="pk-error" style={{ fontWeight: 400 }}>{aiErr}</p>}
        </div>
      )}

      {searched && results === null && <Skeletons />}
      {searched && results && (
        <>
          {err && <div className="pk-pad pk-error" role="alert">{err}</div>}
          {results.length > 0 && <div className="pk-list-heading">Your original thoughts · {results.length}</div>}
          {results.map((r) => <ResultRow key={r.thought_id} r={r} />)}
          {!results.length && !err && (
            <Empty title="No thoughts found." body={mode === "exact" ? "Nothing contains these words. Try Ask AI for meaning-based search." : "Try different words, or a topic like #ideas."} />
          )}
        </>
      )}
    </>
  );
}

function ResultRow({ r }: { r: SearchResult }) {
  const app = useApp();
  return (
    <article className="pk-card" style={{ paddingBottom: 14 }} onClick={() => app.go("detail", r.thought_id)}>
      <div className="pk-avatar">{app.initial}</div>
      <div style={{ minWidth: 0 }}>
        <div className="pk-card-head">
          <strong>{app.name}</strong>
          <span className="pk-muted" title={full(r.created_at)}>· {rel(r.created_at)}</span>
          <span className="pk-muted" style={{ marginLeft: "auto", fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase" }}>
            {MATCH_LABEL[r.match_type] ?? r.match_type}
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
