import { useEffect, useState } from "react";
import { get, type OnThisDay, type Page, type SearchResponse, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { useMedia } from "../app/hooks";
import { rel, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

export const QUICK_AI = [
  "Summarize what I wrote in the last two weeks",
  "Find my unfinished ideas",
  "What did I think about most last month?",
];

/** Context column on wide screens: memories, topics, related and forgotten ideas. */
export function RightPanel() {
  const app = useApp();
  const wide = useMedia("(min-width: 1140px)");
  const [otd, setOtd] = useState<Thought | null>(null);
  const [otdLabel, setOtdLabel] = useState("");
  const [related, setRelated] = useState<{ id: string; date: string; text: string }[]>([]);
  const [forgotten, setForgotten] = useState<Thought[]>([]);

  useEffect(() => {
    if (!wide) return;
    get<OnThisDay>("/memories/on-this-day").then((r) => {
      const g = r.groups[0];
      if (g?.thoughts[0]) {
        setOtd(g.thoughts[0]);
        setOtdLabel(g.years_ago + (g.years_ago === 1 ? " year ago" : " years ago") + " you wrote…");
      }
    }, () => {});
    get<Thought[]>("/memories/forgotten", { limit: 3, older_than_days: 30 }).then(setForgotten, () => {});
    get<Page<Thought>>("/thoughts", { limit: 1 }).then(async (p) => {
      const latest = p.items[0];
      if (!latest) return;
      try {
        const r = await get<SearchResponse>(`/thoughts/${latest.id}/similar`, { limit: 3 });
        setRelated(r.results.map((x) => ({ id: x.thought_id, date: short(x.created_at), text: snip(x.snippet, 70) })));
      } catch {
        /* semantic search is off — the section stays hidden */
      }
    }, () => {});
  }, [wide]);

  if (!wide) return null;
  const topics = app.tags.slice(0, 7);

  return (
    <aside className="pk-right" aria-label="Context">
      <button className="pk-ask-btn" onClick={() => app.go("search")}>
        <Icon d={I.spark} size="sm" className="accent-stroke" />
        <span style={{ flex: 1 }}>Ask your thoughts…</span>
        <span style={{ border: "1px solid var(--color-divider)", padding: "0 6px", fontSize: 12 }}>/</span>
      </button>
      {otd && (
        <button className="pk-otd-card" onClick={() => app.go("detail", otd.id)}>
          <span className="pk-section-label" style={{ display: "block" }}>On this day</span>
          <span style={{ display: "block", fontSize: 13, marginTop: 8 }}>{otdLabel}</span>
          <span className="q">“{snip(otd.content || "Voice note", 160)}”</span>
        </button>
      )}
      {topics.length > 0 && (
        <div>
          <div className="pk-section-label ruled">Popular topics</div>
          {topics.map((t) => (
            <button key={t.id} className="pk-rp-row" onClick={() => app.go("topics", t.name)}>
              <span style={{ fontWeight: 600 }}>#{t.name}</span>
              <span className="pk-muted" style={{ fontVariantNumeric: "tabular-nums" }}>{t.usage_count ?? ""}</span>
            </button>
          ))}
        </div>
      )}
      {related.length > 0 && (
        <div>
          <div className="pk-section-label ruled">Related to your last thought</div>
          {related.map((r) => (
            <button key={r.id} className="pk-rp-item" onClick={() => app.go("detail", r.id)}>
              <span className="d">{r.date}</span>{r.text}
            </button>
          ))}
        </div>
      )}
      {forgotten.length > 0 && (
        <div>
          <div className="pk-section-label ruled">Forgotten ideas</div>
          {forgotten.map((t) => (
            <div key={t.id} className="pk-rp-item" role="button" tabIndex={0} onClick={() => app.go("detail", t.id)}
              onKeyDown={(e) => e.key === "Enter" && app.go("detail", t.id)}>
              <div>{snip(t.content || "Voice note", 64)}</div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 12 }}>
                <span className="pk-muted">{rel(t.created_at)}</span>
                <button style={{ border: 0, background: "none", padding: 0, cursor: "pointer", color: "var(--color-accent-700)", fontWeight: 600, fontSize: 12 }}
                  onClick={(e) => { e.stopPropagation(); app.reply(t); }}>Continue →</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div>
        <div className="pk-section-label ruled">Quick AI</div>
        {QUICK_AI.map((q) => (
          <button key={q} className="pk-rp-item" style={{ display: "flex", gap: 8 }} onClick={() => app.go("search", q)}>
            <span style={{ color: "var(--color-accent)" }}>→</span>{q}
          </button>
        ))}
      </div>
    </aside>
  );
}
