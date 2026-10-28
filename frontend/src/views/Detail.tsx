import { useEffect, useState, type KeyboardEvent } from "react";
import {
  ApiError, errorMessage, get, patch, post, type Relation, type Revision, type SearchResponse, type Thought, type Thread,
} from "../api/client";
import { emit, useThoughtEvents } from "../app/bus";
import { TYPE_LABEL, useApp } from "../app/context";
import { useLoad } from "../app/hooks";
import { Empty, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { Insights } from "../components/Insights";
import { ThoughtMedia, useScrollIntoView } from "../components/Media";
import { full, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

interface Linked {
  rel: Relation;
  other: Thought | null;
}

export function DetailView({ id }: { id: string }) {
  const app = useApp();
  const main = useLoad((s) => get<Thought>(`/thoughts/${id}`, undefined, s), [id]);
  const [t, setT] = useState<Thought | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [links, setLinks] = useState<Linked[]>([]);
  const [similar, setSimilar] = useState<SearchResponse["results"]>([]);
  const [cont, setCont] = useState("");
  const [posting, setPosting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");

  useEffect(() => setT(main.data), [main.data]);
  useThoughtEvents((e) => {
    if (e.type === "updated" && e.thought.id === id) setT(e.thought);
    if (e.type === "updated" && thread) {
      setThread({ ...thread, items: thread.items.map((n) => (n.thought.id === e.thought.id ? { ...n, thought: e.thought } : n)) });
    }
  });

  const threadId = t?.thread_id ?? null;
  useEffect(() => {
    if (!threadId) return setThread(null);
    get<Thread>(`/threads/${threadId}`).then(setThread, () => setThread(null));
  }, [threadId, t?.reply_count]);

  const loadRelations = () =>
    get<Relation[]>(`/thoughts/${id}/relations`).then(async (rels) => {
      const live = rels.filter((r) => r.status !== "rejected");
      const others = await Promise.all(
        live.map((r) => {
          const other = r.source_thought_id === id ? r.target_thought_id : r.source_thought_id;
          return get<Thought>(`/thoughts/${other}`).catch(() => null);
        }),
      );
      setLinks(live.map((rel, i) => ({ rel, other: others[i] })).filter((l) => l.other));
    }, () => {});

  useEffect(() => {
    get<Revision[]>(`/thoughts/${id}/revisions`).then(setRevisions, () => {});
    loadRelations();
    get<SearchResponse>(`/thoughts/${id}/similar`, { limit: 4 }).then((r) => setSimilar(r.results), () => setSimilar([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (main.error) {
    return (
      <>
        <Header title="Thought" back />
        <Empty title="This thought isn't here." body={main.error} cta="Back to timeline" onCta={() => app.go("home")} />
      </>
    );
  }
  if (!t) return (<><Header title="Thought" back /><Skeletons n={1} /></>);

  const nodes = thread && thread.items.length > 1 ? thread.items : null;
  const members = nodes ? nodes.map((n) => n.thought) : [t];
  const k = members.findIndex((x) => x.id === t.id);
  const prev = nodes && k > 0 ? members[k - 1].id : null;
  const next = nodes && k < members.length - 1 ? members[k + 1].id : null;
  const confirmed = links.filter((l) => l.rel.status === "confirmed");
  const suggested = links.filter((l) => l.rel.status === "suggested");
  const linkedIds = new Set(links.map((l) => l.other!.id));
  const memberIds = new Set(members.map((m) => m.id));
  const relatedByTopic = similar.filter((s) => !linkedIds.has(s.thought_id) && !memberIds.has(s.thought_id)).slice(0, 3);
  const history = revisions.filter((r) => r.previous_content !== null && r.previous_content !== undefined).reverse();

  async function continueThought() {
    if (!cont.trim() || !t || posting) return;
    setPosting(true);
    try {
      const created = await post<Thought>(`/thoughts/${t.id}/replies`, {
        content: cont.trim(), thought_type: t.thought_type, project_ids: t.projects.map((p) => p.id),
      });
      emit({ type: "created", thought: created });
      emit({ type: "updated", thought: { ...t, reply_count: (t.reply_count ?? 0) + 1 } });
      setCont("");
      app.toast("Thought continued");
      app.go("detail", created.id);
    } catch (e) {
      app.toast(errorMessage(e));
    } finally {
      setPosting(false);
    }
  }

  async function saveEdit() {
    if (!t) return;
    try {
      const updated = await patch<Thought>(`/thoughts/${t.id}`, { expected_version: t.version, content: editText });
      emit({ type: "updated", thought: updated });
      setEditing(false);
      get<Revision[]>(`/thoughts/${id}/revisions`).then(setRevisions, () => {});
      if (updated.tags.join() !== t.tags.join()) app.reloadMeta();
      app.toast("Saved — the previous version is kept in the edit history");
    } catch (e) {
      app.toast(e instanceof ApiError && e.code === "version_conflict" ? "Changed elsewhere — reloading" : errorMessage(e));
      if (e instanceof ApiError && e.code === "version_conflict") main.reload();
    }
  }

  async function judge(l: Linked, verdict: "confirm" | "reject") {
    try {
      await post(`/relations/${l.rel.id}/${verdict}`);
      loadRelations();
    } catch (e) {
      app.toast(errorMessage(e));
    }
  }

  const onContKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      continueThought();
    }
  };

  const meta: [string, string][] = [
    ["Created", full(t.created_at)],
    ["Last edited", revisions.length > 1 ? full(t.updated_at) : "Never edited"],
    ["Category", TYPE_LABEL[t.thought_type] ?? t.thought_type],
    ["Topics", t.tags.length ? t.tags.map((g) => "#" + g).join("  ") : "—"],
    ["Project", t.projects.map((p) => p.name).join(", ") || "—"],
    ["Thread", nodes ? `${members.length} parts · started ${short(members[0].created_at)}` : "Standalone"],
    ["Source", t.source],
  ];

  return (
    <>
      <Header title="Thought" back right={nodes && (
        <div className="pk-thread-nav">
          <button disabled={!prev} onClick={() => prev && app.go("detail", prev)} aria-label="Previous part"><Icon d={I.left} size="sm" /></button>
          <span className="pk-thread-pos">{k + 1} / {members.length}</span>
          <button disabled={!next} onClick={() => next && app.go("detail", next)} aria-label="Next part"><Icon d={I.right} size="sm" /></button>
        </div>
      )} />
      <div className="pk-thread">
        {members.map((m, i) => (
          <ThreadNode key={m.id} m={m} focus={m.id === t.id} part={nodes ? i + 1 : 0} hideText={editing && m.id === t.id}>
            {m.id === t.id && (
              <>
                {editing ? (
                  <div className="pk-editor" style={{ marginTop: 8 }}>
                    <textarea className="input" value={editText} onChange={(e) => setEditText(e.target.value)} autoFocus
                      onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); saveEdit(); } }} />
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                      <button className="btn btn-primary" onClick={saveEdit} disabled={editText === t.content}>Save</button>
                      <button className="btn btn-secondary" onClick={() => setEditing(false)}>Cancel</button>
                    </div>
                  </div>
                ) : null}
                <ThoughtMedia attachments={t.attachments} text={t.content} large />
                <div className="pk-meta" style={{ marginTop: 12 }}>
                  {t.projects.map((p) => <button key={p.id} className="pk-proj-chip" onClick={() => app.go("project", p.id)}>{p.name}</button>)}
                  {t.tags.map((g) => <button key={g} className="tag tag-neutral pk-tag-btn" onClick={() => app.go("topics", g)}>#{g}</button>)}
                </div>
                <div className="pk-detail-bar">
                  <button className={t.is_bookmarked ? "on" : ""} onClick={() => app.toggleBookmark(t)}>
                    <Icon d={I.bookmark} size="sm" />{t.is_bookmarked ? "Bookmarked" : "Bookmark"}
                  </button>
                  <button onClick={() => app.openConnect(t)}><Icon d={I.link} size="sm" />Connect</button>
                  <button onClick={() => { setEditText(t.content); setEditing(true); }}>Edit</button>
                  <button onClick={() => navigator.clipboard?.writeText(t.content).then(() => app.toast("Copied to clipboard"), () => {})}>Copy</button>
                  <button className="danger" onClick={() => app.askDelete(t)}>Delete</button>
                </div>
              </>
            )}
          </ThreadNode>
        ))}
        <div className="pk-continue">
          <div className="pk-continue-mark"><Icon d={I.plus} size="sm" /></div>
          <div>
            <textarea rows={2} aria-label="Continue this thought" placeholder="Continue this thought…" value={cont}
              onChange={(e) => { setCont(e.target.value); e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; }}
              onKeyDown={onContKey} />
            <div className="pk-continue-foot">
              <span>Becomes the next part of this thread · Ctrl + Enter</span>
              <button className="btn btn-primary" style={{ minHeight: 34 }} disabled={!cont.trim() || posting} onClick={continueThought}>Continue</button>
            </div>
          </div>
        </div>
      </div>

      <Insights key={t.id} thoughtId={t.id} threadId={nodes ? t.thread_id : null} boxed />

      <div className="pk-detail-extra">
        {confirmed.length > 0 && (
          <div>
            <div className="pk-section-label ruled">Connected thoughts</div>
            {confirmed.map((l) => (
              <button key={l.rel.id} className="pk-rowlink big" onClick={() => app.go("detail", l.other!.id)}>
                <span className="d">{short(l.other!.created_at)}</span>{snip(l.other!.content || "Voice note", 100)}
              </button>
            ))}
          </div>
        )}
        {suggested.length > 0 && (
          <div>
            <div className="pk-section-label ruled">Suggested by AI · not confirmed</div>
            {suggested.map((l) => (
              <div key={l.rel.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--color-divider)" }}>
                <button className="pk-rowlink" style={{ border: 0, padding: 0, flex: 1 }} onClick={() => app.go("detail", l.other!.id)}>
                  <span className="d" style={{ display: "block", fontSize: 12 }}>{l.rel.relation_type.replace("_", " ")}{l.rel.rationale ? " · " + l.rel.rationale : ""}</span>
                  {snip(l.other!.content || "Voice note", 100)}
                </button>
                <button className="btn btn-secondary" onClick={() => judge(l, "confirm")}>Confirm</button>
                <button className="btn btn-ghost" onClick={() => judge(l, "reject")}>Reject</button>
              </div>
            ))}
          </div>
        )}
        {relatedByTopic.length > 0 && (
          <div>
            <div className="pk-section-label ruled">Related by meaning</div>
            {relatedByTopic.map((r) => (
              <button key={r.thought_id} className="pk-rowlink big" onClick={() => app.go("detail", r.thought_id)}>
                <span className="d">{short(r.created_at)}</span>{snip(r.snippet, 100)}
              </button>
            ))}
          </div>
        )}
        <div>
          <div className="pk-section-label ruled">Details</div>
          {meta.map(([kk, v]) => <div key={kk} className="pk-kv"><span>{kk}</span><span>{v}</span></div>)}
        </div>
        {history.length > 0 && (
          <div>
            <div className="pk-section-label ruled">Edit history</div>
            {history.map((h) => (
              <div key={h.id} className="pk-history">
                <div className="pk-muted" style={{ fontSize: 12 }}>Before edit {h.revision_number - 1} · {full(h.created_at)}</div>
                <del>{h.previous_content}</del>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function ThreadNode({ m, focus, part, hideText, children }: {
  m: Thought; focus: boolean; part: number; hideText?: boolean; children: React.ReactNode;
}) {
  const app = useApp();
  const ref = useScrollIntoView<HTMLDivElement>(focus && part > 1);
  const text = hideText ? "" : focus ? m.content : snip(m.content || "Voice note", 220);
  return (
    <div ref={ref} className={"pk-tnode " + (focus ? "focus" : "other")} onClick={focus ? undefined : () => app.go("detail", m.id)}>
      <div className="pk-tnode-rail"><div className="pk-avatar">{app.initial}</div><div className="line" /></div>
      <div className="pk-tnode-body">
        <div className="pk-tnode-head">
          <strong>{app.name}</strong><span>{full(m.created_at)}</span>
          {part > 0 && <span className="pk-part">Part {part}</span>}
        </div>
        {text && <div className={"pk-tnode-text" + (focus && m.content.length < 140 ? " short" : "")}>{text}</div>}
        {children}
      </div>
    </div>
  );
}
