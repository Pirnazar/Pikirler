import { useState } from "react";
import type { Thought } from "../api/client";
import { TYPE_LABEL, useApp } from "../app/context";
import { full, rel } from "../lib/format";
import { I, Icon } from "../lib/icons";
import { Insights } from "./Insights";
import { ThoughtMedia } from "./Media";

export function ThoughtCard({ t, fresh }: { t: Thought; fresh?: boolean }) {
  const app = useApp();
  const [expanded, setExpanded] = useState(false);
  const [menu, setMenu] = useState(false);
  const [ai, setAi] = useState(false);
  const [pop, setPop] = useState(false);
  const long = t.content.length > 300;
  const text = long && !expanded ? t.content.slice(0, 270).trimEnd() + "…" : t.content;
  const cat = t.thought_type !== "thought" ? TYPE_LABEL[t.thought_type] : "";
  const proj = t.projects[0];
  const inThread = !!t.parent_id || (t.reply_count ?? 0) > 0;
  const stop = (fn: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    fn();
  };
  const open = () => app.go("detail", t.id);

  return (
    <article className={"pk-card" + (fresh ? " new" : "")} onClick={open} onMouseLeave={() => setMenu(false)}>
      <div className="pk-avatar">{app.initial}</div>
      <div style={{ minWidth: 0 }}>
        <div className="pk-card-head">
          <strong>{app.name}</strong>
          <button className="pk-date-btn" title={full(t.created_at)} onClick={stop(open)}>· {rel(t.created_at)}</button>
          {cat && <span className="tag tag-accent pk-cat">{cat}</span>}
          {t.status === "draft" && <span className="pk-muted" style={{ fontSize: 12 }}>· draft</span>}
          {t.is_pinned && <span className="pk-muted" style={{ fontSize: 12 }}>· pinned</span>}
        </div>
        {t.content && <div className="pk-text">{text}</div>}
        {long && (
          <button className="pk-more" onClick={stop(() => setExpanded((x) => !x))}>{expanded ? "Show less" : "Read more"}</button>
        )}
        <ThoughtMedia attachments={t.attachments} text={t.content} />
        {(proj || t.tags.length > 0 || inThread) && (
          <div className="pk-meta">
            {proj && <button className="pk-proj-chip" onClick={stop(() => app.go("project", proj.id))}>{proj.name}</button>}
            {t.tags.map((g) => (
              <button key={g} className="tag tag-neutral pk-tag-btn" onClick={stop(() => app.go("topics", g))}>#{g}</button>
            ))}
            {inThread && (
              <span className="pk-thread-label">
                ↳ {t.parent_id ? "Part of a thread" : `Thread · ${t.reply_count} ${t.reply_count === 1 ? "reply" : "replies"}`}
              </span>
            )}
          </div>
        )}
        <div className="pk-actions">
          <button className="pk-act" title="Continue thought" onClick={stop(() => app.reply(t))}>
            <Icon d={I.reply} size="sm" />Continue
          </button>
          <button className={"pk-act" + (t.is_bookmarked ? " on" : "") + (pop ? " pop" : "")}
            aria-label={t.is_bookmarked ? "Remove bookmark" : "Bookmark"} title={t.is_bookmarked ? "Remove bookmark" : "Bookmark"}
            onClick={stop(() => { setPop(!t.is_bookmarked); app.toggleBookmark(t); })}>
            <Icon d={I.bookmark} size="sm" fill={t.is_bookmarked} />
          </button>
          <div className={"pk-act-extra" + (menu || ai ? " show" : "")}>
            <button className="pk-act" title="Connect thoughts" onClick={stop(() => app.openConnect(t))}>
              <Icon d={I.link} size="sm" />
            </button>
            <button className={"pk-act" + (ai ? " on" : "")} title="AI analyze" onClick={stop(() => setAi((x) => !x))}>
              <Icon d={I.spark} size="sm" />Analyze
            </button>
            <button className="pk-act" aria-label="More" style={{ marginLeft: "auto" }} onClick={stop(() => setMenu((x) => !x))}>
              <Icon d={I.dots} size="sm" />
            </button>
          </div>
          {menu && (
            <div role="menu" className="pk-menu" onClick={(e) => e.stopPropagation()}>
              <button role="menuitem" onClick={open}>Open thought</button>
              <button role="menuitem" onClick={() => {
                navigator.clipboard?.writeText(t.content).then(() => app.toast("Copied to clipboard"), () => app.toast("Couldn't copy"));
                setMenu(false);
              }}>Copy text</button>
              <button role="menuitem" className="danger" onClick={() => { setMenu(false); app.askDelete(t); }}>Delete…</button>
            </div>
          )}
        </div>
        {ai && <Insights thoughtId={t.id} threadId={null} />}
      </div>
    </article>
  );
}
