import { useState } from "react";
import { errorMessage, get, post, type Activity, type Page, type Thought } from "../api/client";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";
import { useLoad, usePaged } from "../app/hooks";
import { Empty, Feed, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { I, Icon } from "../lib/icons";
import { rel, snip } from "../lib/format";

const TABS = [["all", "f_all"], ["threads", "tab_threads"], ["voice", "tab_voice"], ["media", "f_media"], ["drafts", "tab_drafts"], ["trash", "tab_trash"]] as const;

export function MineView() {
  const app = useApp();
  const { t } = useI18n();
  const [tab, setTab] = useState("all");
  const year = new Date().getFullYear();
  const activity = useLoad((s) => get<Activity>("/memories/activity", { year }, s), [year]);
  const days = activity.data?.days.filter((d) => d.count > 0).length ?? 0;
  const stats: [string, string][] = [
    [t("stat_year", { year }), activity.data ? String(activity.data.total) : "…"],
    [t("stat_days"), activity.data ? String(days) : "…"],
    [t("nav_topics"), String(app.tags.length)],
    [t("nav_projects"), String(app.projects.length)],
  ];

  return (
    <>
      <Header title={t("nav_mine")} tabs={TABS.map(([k, l]) => [k, t(l)] as [string, string])} active={tab} onTab={setTab}
        right={<button className="pk-square-btn" onClick={() => app.go("settings")} aria-label={t("nav_settings")}><Icon d={I.settings} size="sm" /></button>} />
      <div className="pk-pad pk-ruled">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div className="pk-avatar" style={{ width: 56, height: 56, fontSize: 24 }}>{app.initial}</div>
          <div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{app.name}</div>
            <div className="pk-muted" style={{ fontSize: 13 }}>{t("private_space_email", { email: app.session.owner.email })}</div>
          </div>
        </div>
        <div className="pk-stat-row pk-cols-4" style={{ display: "grid" }}>
          {stats.map(([l, n]) => (
            <div key={l} style={{ padding: "12px 10px 0 0" }}><div className="n">{n}</div><div className="l">{l}</div></div>
          ))}
        </div>
        {app.isMobile && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 16 }}>
            {([["nav_bookmarks", "bookmarks"], ["nav_topics", "topics"], ["nav_projects", "projects"], ["nav_calendar", "calendar"], ["nav_settings", "settings"]] as const).map(([l, k]) => (
              <button key={k} className="btn btn-secondary" style={{ minHeight: 40 }} onClick={() => app.go(k)}>{t(l)}</button>
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
  const { t } = useI18n();
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
      <Empty title={t("nothing_here")} body={tab === "drafts" ? t("drafts_empty") : t("kind_empty")}
        cta={tab === "all" ? t("write_a_thought") : undefined} onCta={() => app.newThought()} />
    } />
  );
}

function Trash() {
  const app = useApp();
  const { t } = useI18n();
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/thoughts", { limit: 50, cursor, deleted: true }, signal), []);
  const [gone, setGone] = useState<Set<string>>(new Set());
  if (!feed.items) return <Skeletons />;
  const items = feed.items.filter((t) => !gone.has(t.id));
  async function restore(th: Thought) {
    try {
      const r = await post<Thought>(`/thoughts/${th.id}/restore`);
      setGone((s) => new Set(s).add(th.id));
      emit({ type: "restored", thought: r });
      app.toast(t("thought_restored"));
    } catch (e) {
      app.toast(errorMessage(e));
    }
  }
  return (
    <>
      <div className="pk-list-heading">{t("trash_heading")}</div>
      {!items.length && <Empty title={t("trash_empty")} />}
      {items.map((th) => (
        <div key={th.id} style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px 20px", borderBottom: "1px solid var(--color-divider)" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="pk-muted" style={{ fontSize: 12 }}>{t("deleted_when", { when: th.deleted_at ? rel(th.deleted_at) : "" })}</div>
            <div style={{ fontSize: 15 }}>{snip(th.content || t("voice_note"), 140)}</div>
          </div>
          <button className="btn btn-secondary" onClick={() => restore(th)}>{t("restore")}</button>
        </div>
      ))}
      {feed.hasMore && <div className="pk-loadmore"><button className="btn btn-secondary" onClick={feed.more}>{t("show_more")}</button></div>}
    </>
  );
}

export function BookmarksView() {
  const app = useApp();
  const { t } = useI18n();
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/timeline/bookmarked", { limit: 30, cursor }, signal), [], {
    keep: (t) => t.is_bookmarked,
    acceptsNew: (t) => t.is_bookmarked,
  });
  return (
    <>
      <Header title={t("nav_bookmarks")} sub={t("bookmarks_sub")} />
      <Feed {...feed} empty={<Empty title={t("no_bookmarks")} body={t("no_bookmarks_body")} cta={t("go_timeline")} onCta={() => app.go("home")} />} />
    </>
  );
}
