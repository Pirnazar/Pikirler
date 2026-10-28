import { useState } from "react";
import {
  errorMessage, post, type ActionsResponse, type EvolutionResponse, type ExpandResponse,
  type RelatedResponse, type SourceRef, type SummaryResponse,
} from "../api/client";
import { useApp } from "../app/context";
import { useI18n, type I18n } from "../i18n";
import { short, snip } from "../lib/format";

type Action = "related" | "summarize" | "expand" | "next" | "connections";
const ACTIONS = [
  ["related", "ai_find_related"], ["summarize", "ai_summarize"], ["expand", "ai_expand"],
  ["next", "ai_next"], ["connections", "ai_connections"],
] as const;

interface Result {
  text: string;
  related: SourceRef[];
  model: string;
}

async function run(action: Action, thoughtId: string, threadId: string | null, t: I18n["t"]): Promise<Result> {
  const by = (r: { provider: string; model: string }) => `${r.provider} · ${r.model}`;
  switch (action) {
    case "related": {
      const r = await post<RelatedResponse>("/ai/related", { thought_id: thoughtId, limit: 6, suggest_relations: false });
      return { text: "", related: r.similar, model: r.provider ? `${r.provider} · ${r.model}` : "" };
    }
    case "summarize": {
      const r = await post<SummaryResponse>("/ai/summarize", threadId ? { thread_id: threadId } : { thought_id: thoughtId });
      const points = r.key_points.length ? "\n\n" + r.key_points.map((p) => "— " + p).join("\n") : "";
      return { text: r.summary + points, related: [], model: by(r) };
    }
    case "expand": {
      const r = await post<ExpandResponse>("/ai/expand", { thought_id: thoughtId });
      const parts = [r.analysis];
      if (r.next_steps.length) parts.push(r.next_steps.map((s, i) => `${i + 1}. ${s}`).join("\n"));
      if (r.open_questions.length) parts.push(t("ai_open_questions") + "\n" + r.open_questions.map((q) => "— " + q).join("\n"));
      return { text: parts.join("\n\n"), related: [], model: by(r) };
    }
    case "next": {
      const r = await post<ActionsResponse>("/ai/extract-actions", threadId ? { thread_id: threadId } : { thought_id: thoughtId });
      return {
        text: r.actions.length ? r.actions.map((a, i) => `${i + 1}. ${a.text}`).join("\n") : t("ai_no_steps"),
        related: [], model: by(r),
      };
    }
    case "connections": {
      const r = await post<EvolutionResponse>("/ai/evolution", { thought_id: thoughtId });
      const stages = r.stages.map((s) => "— " + s.summary).join("\n");
      const contra = r.contradictions.length ? "\n\n" + t("ai_tensions") + "\n" + r.contradictions.map((c) => "— " + c.explanation).join("\n") : "";
      return { text: [r.narrative, stages].filter(Boolean).join("\n\n") + contra, related: r.sources, model: by(r) };
    }
  }
}

/** "AI insights": generated analysis, always shown apart from the owner's words. */
export function Insights({ thoughtId, threadId, boxed }: { thoughtId: string; threadId: string | null; boxed?: boolean }) {
  const app = useApp();
  const { t } = useI18n();
  const [action, setAction] = useState<Action | null>(null);
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");

  async function start(a: Action) {
    setAction(a);
    setRes(null);
    setErr("");
    setLoading(true);
    try {
      const r = await run(a, thoughtId, threadId, t);
      setRes(r);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={"pk-insights" + (boxed ? " boxed" : "")} onClick={(e) => e.stopPropagation()}>
      <div className="pk-insights-title">
        {t("ai_insights")} <span>· {boxed ? t("ai_kept_separate") : t("ai_not_your_words")}</span>
      </div>
      <div className="pk-chips">
        {ACTIONS.map(([k, label]) => (
          <button key={k} className="pk-chip" aria-pressed={action === k} disabled={loading} onClick={() => start(k)}>{t(label)}</button>
        ))}
      </div>
      {loading && <div className="pk-loading-line" style={{ marginTop: 12 }}>{t("ai_reading")}</div>}
      {res?.text && (
        <>
          <div className="pk-ai-text" style={boxed ? { fontSize: 15, lineHeight: 1.6 } : undefined}>{res.text}</div>
          {res.model && <div className="pk-ai-foot">{t("ai_generated_by", { model: res.model })}</div>}
        </>
      )}
      {err && <div role="alert" className="pk-error" style={{ marginTop: 12, fontWeight: 400 }}>{err}</div>}
      {res && res.related.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {res.related.map((r) => (
            <button key={r.thought_id} className="pk-rowlink" onClick={() => app.go("detail", r.thought_id)}>
              <span className="d">{short(r.created_at)} — </span>{snip(r.snippet, 100)}
            </button>
          ))}
        </div>
      )}
      {res && action === "related" && !res.related.length && (
        <div className="pk-muted" style={{ fontSize: 13, marginTop: 10 }}>{t("ai_no_related")}</div>
      )}
    </div>
  );
}
