import { useEffect, useState } from "react";
import { get, type OnThisDay, type Page, type SearchResponse, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { useMedia } from "../app/hooks";
import { useI18n } from "../i18n";
import { rel, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

export const QUICK_AI = ["quick_1", "quick_2", "quick_3"] as const;

/** Context column on wide screens: memories, topics, related and forgotten ideas. */
export function RightPanel() {
  const app = useApp();
  const { t, tn, lang } = useI18n();
  const wide = useMedia("(min-width: 1140px)");
  const [otd, setOtd] = useState<Thought | null>(null);
  const [otdYears, setOtdYears] = useState(0);
  const [related, setRelated] = useState<{ id: string; date: string; text: string }[]>([]);
  const [forgotten, setForgotten] = useState<Thought[]>([]);

  useEffect(() => {
    if (!wide) return;
    get<OnThisDay>("/memories/on-this-day").then((r) => {
      const g = r.groups[0];
      if (g?.thoughts[0]) {
        setOtd(g.thoughts[0]);
        setOtdYears(g.years_ago);
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
    <aside className="pk-right" aria-label={t("context")}>
      <button className="pk-ask-btn" onClick={() => app.go("search")}>
        <Icon d={I.spark} size="sm" className="accent-stroke" />
        <span style={{ flex: 1 }}>{t("ask_thoughts")}</span>
        <span style={{ border: "1px solid var(--color-divider)", padding: "0 6px", fontSize: 12 }}>/</span>
      </button>
      {otd && (
        <button className="pk-otd-card" onClick={() => app.go("detail", otd.id)}>
          <span className="pk-section-label" style={{ display: "block" }}>{t("on_this_day")}</span>
          <span style={{ display: "block", fontSize: 13, marginTop: 8 }}>{tn("years_ago_wrote", otdYears)}</span>
          <span className="q">“{snip(otd.content || t("voice_note"), 160)}”</span>
        </button>
      )}
      {topics.length > 0 && (
        <div>
          <div className="pk-section-label ruled">{t("popular_topics")}</div>
          {topics.map((tg) => (
            <button key={tg.id} className="pk-rp-row" onClick={() => app.go("topics", tg.name)}>
              <span style={{ fontWeight: 600 }}>#{tg.name}</span>
              <span className="pk-muted" style={{ fontVariantNumeric: "tabular-nums" }}>{tg.usage_count ?? ""}</span>
            </button>
          ))}
        </div>
      )}
      {related.length > 0 && (
        <div>
          <div className="pk-section-label ruled">{t("related_last")}</div>
          {related.map((r) => (
            <button key={r.id} className="pk-rp-item" onClick={() => app.go("detail", r.id)}>
              <span className="d">{r.date}</span>{r.text}
            </button>
          ))}
        </div>
      )}
      {forgotten.length > 0 && (
        <div>
          <div className="pk-section-label ruled">{t("forgotten_ideas")}</div>
          {forgotten.map((th) => (
            <div key={th.id} className="pk-rp-item" role="button" tabIndex={0} onClick={() => app.go("detail", th.id)}
              onKeyDown={(e) => e.key === "Enter" && app.go("detail", th.id)}>
              <div>{snip(th.content || t("voice_note"), 64)}</div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 12 }}>
                <span className="pk-muted">{rel(th.created_at)}</span>
                <button style={{ border: 0, background: "none", padding: 0, cursor: "pointer", color: "var(--color-accent-700)", fontWeight: 600, fontSize: 12 }}
                  onClick={(e) => { e.stopPropagation(); app.reply(th); }}>{t("continue_arrow")}</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div>
        <div className="pk-section-label ruled">{t("quick_ai")}</div>
        {QUICK_AI.map((k) => (
          <button key={k + lang} className="pk-rp-item" style={{ display: "flex", gap: 8 }} onClick={() => app.go("search", t(k))}>
            <span style={{ color: "var(--color-accent)" }}>→</span>{t(k)}
          </button>
        ))}
      </div>
    </aside>
  );
}
