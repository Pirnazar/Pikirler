import { useState } from "react";
import { errorMessage, get, post, type Activity, type Page, type Thought } from "../api/client";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useLoad, usePaged } from "../app/hooks";
import { Empty, Feed, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { I, Icon } from "../lib/icons";
import { rel, snip } from "../lib/format";

const TABS: [string, string][] = [["all", "All"], ["threads", "Threads"], ["voice", "Voice"], ["media", "Media"], ["drafts", "Drafts"], ["trash", "Trash"]];

export function MineView() {
  const app = useApp();
  const [tab, setTab] = useState("all");
  const year = new Date().getFullYear();
  const activity = useLoad((s) => get<Activity>("/memories/activity", { year }, s), [year]);
  const days = activity.data?.days.filter((d) => d.count > 0).length ?? 0;
  const stats: [string, string][] = [
    ["Thoughts in " + year, activity.data ? String(activity.data.total) : "…"],
    ["Days written", activity.data ? String(days) : "…"],
    ["Topics", String(app.tags.length)],
    ["Projects", String(app.projects.length)],
  ];

  return (
    <>
      <Header title="My Thoughts" tabs={TABS} active={tab} onTab={setTab}
        right={<button className="pk-square-btn" onClick={() => app.go("settings")} aria-label="Settings"><Icon d={I.settings} size="sm" /></button>} />
      <div className="pk-pad pk-ruled">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div className="pk-avatar" style={{ width: 56, height: 56, fontSize: 24 }}>{app.initial}</div>
          <div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{app.name}</div>
            <div className="pk-muted" style={{ fontSize: 13 }}>Private space · {app.session.owner.email}</div>
          </div>
        </div>
        <div className="pk-stat-row pk-cols-4" style={{ display: "grid" }}>
          {stats.map(([l, n]) => (
            <div key={l} style={{ padding: "12px 10px 0 0" }}><div className="n">{n}</div><div className="l">{l}</div></div>
          ))}
        </div>
        {app.isMobile && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 16 }}>
            {([["Bookmarks", "bookmarks"], ["Topics", "topics"], ["Projects", "projects"], ["Calendar", "calendar"], ["Settings", "settings"]] as const).map(([l, k]) => (
              <button key={k} className="btn btn-secondary" style={{ minHeight: 40 }} onClick={() => app.go(k)}>{l}</button>
            ))}
          </div>
        )}
      </div>
      {tab === "trash" ? <Trash /> : <MineList key={tab} tab={tab} />}
    </>
  );
}

function MineList({ tab }: { tab: string }) {
  const app = useApp();
  const media = tab === "voice" || tab === "media";
  const keep = (t: Thought) =>
    tab === "threads" ? !!t.parent_id || (t.reply_count ?? 0) > 0
      : tab === "voice" ? t.attachments.some((a) => a.kind === "audio")
        : tab === "media" ? t.attachments.some((a) => a.kind !== "audio")
          : true;
  const feed = usePaged(
    (cursor, signal) => media
      ? get<Page<Thought>>("/timeline/media", { limit: 50, cursor }, signal)
      : get<Page<Thought>>("/thoughts", { limit: 50, cursor, status: tab === "drafts" ? "draft" : undefined }, signal),
    [tab],
    { keep, acceptsNew: (t) => keep(t) && (tab === "drafts" ? t.status === "draft" : t.status !== "draft") },
  );
  return (
    <Feed {...feed} empty={
      <Empty title="Nothing here yet." body={tab === "drafts" ? "Drafts saved through the API or Jemşit show up here." : "Thoughts of this kind will collect here."}
        cta={tab === "all" ? "Write a thought" : undefined} onCta={() => app.newThought()} />
    } />
  );
}

function Trash() {
  const app = useApp();
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/thoughts", { limit: 50, cursor, deleted: true }, signal), []);
  const [gone, setGone] = useState<Set<string>>(new Set());
  if (!feed.items) return <Skeletons />;
  const items = feed.items.filter((t) => !gone.has(t.id));
  async function restore(t: Thought) {
    try {
      const r = await post<Thought>(`/thoughts/${t.id}/restore`);
      setGone((s) => new Set(s).add(t.id));
      emit({ type: "restored", thought: r });
      app.toast("Thought restored");
    } catch (e) {
      app.toast(errorMessage(e));
    }
  }
  return (
    <>
      <div className="pk-list-heading">Trash · deleted thoughts are removed for good after 30 days</div>
      {!items.length && <Empty title="The trash is empty." />}
      {items.map((t) => (
        <div key={t.id} style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px 20px", borderBottom: "1px solid var(--color-divider)" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="pk-muted" style={{ fontSize: 12 }}>Deleted {t.deleted_at ? rel(t.deleted_at) : ""}</div>
            <div style={{ fontSize: 15 }}>{snip(t.content || "Voice note", 140)}</div>
          </div>
          <button className="btn btn-secondary" onClick={() => restore(t)}>Restore</button>
        </div>
      ))}
      {feed.hasMore && <div className="pk-loadmore"><button className="btn btn-secondary" onClick={feed.more}>Show more</button></div>}
    </>
  );
}

export function BookmarksView() {
  const app = useApp();
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/timeline/bookmarked", { limit: 30, cursor }, signal), [], {
    keep: (t) => t.is_bookmarked,
    acceptsNew: (t) => t.is_bookmarked,
  });
  return (
    <>
      <Header title="Bookmarks" sub="Thoughts you wanted to keep close" />
      <Feed {...feed} empty={<Empty title="No bookmarks yet." body="Tap the bookmark on any thought to keep it here." cta="Go to timeline" onCta={() => app.go("home")} />} />
    </>
  );
}
