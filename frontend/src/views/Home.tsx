import { useState } from "react";
import { get, type Page, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";
import { usePaged } from "../app/hooks";
import { Composer } from "../components/Composer";
import { Empty, Feed } from "../components/Feed";
import { Header } from "../components/Header";
import { I, Icon } from "../lib/icons";
import { short } from "../lib/format";

const FILTERS = [
  ["all", "f_all"], ["thought", "f_thoughts"], ["idea", "f_ideas"], ["reflection", "f_reflections"], ["project", "f_projects"], ["media", "f_media"],
] as const;

function loader(filter: string) {
  return (cursor: string | null, signal: AbortSignal) => {
    const q = { limit: 30, cursor };
    switch (filter) {
      case "all": return get<Page<Thought>>("/timeline", q, signal);
      case "project": return get<Page<Thought>>("/timeline/projects", q, signal);
      case "media": return get<Page<Thought>>("/timeline/media", q, signal);
      default: return get<Page<Thought>>("/thoughts", { ...q, type: filter, status: "published" }, signal);
    }
  };
}

function accepts(filter: string, t: Thought): boolean {
  if (t.status !== "published") return false;
  if (filter === "all") return true;
  if (filter === "project") return t.projects.length > 0;
  if (filter === "media") return t.attachments.length > 0;
  return t.thought_type === filter;
}

export function HomeView() {
  const app = useApp();
  const { t } = useI18n();
  const [filter, setFilter] = useState("all");
  const feed = usePaged(loader(filter), [filter], { acceptsNew: (t) => accepts(filter, t) });
  const items = feed.items && filter === "all" ? [...feed.pinned, ...feed.items.filter((t) => !feed.pinned.some((p) => p.id === t.id))] : feed.items;
  const last = feed.items?.[feed.items.length - 1];

  return (
    <>
      <Header
        title="Pikirler"
        sub={t("home_sub")}
        mark
        tabs={FILTERS.map(([k, l]) => [k, t(l)] as [string, string])}
        active={filter}
        onTab={setFilter}
        right={
          <>
            <button className="pk-square-btn" onClick={() => app.go("search")} aria-label={t("search_kbd")} title={t("search_kbd")}>
              <Icon d={I.search} size="sm" />
            </button>
            {!app.isMobile && (
              <button className="btn btn-primary" style={{ minHeight: 38, gap: 8 }} onClick={() => app.newThought()}>
                <Icon d={I.plus} size="sm" />{t("nav_new")}
              </button>
            )}
          </>
        }
      />
      {!app.isMobile && <Composer variant="inline" />}
      <Feed
        items={items}
        error={feed.error}
        hasMore={feed.hasMore}
        loadingMore={feed.loadingMore}
        more={feed.more}
        endNote={filter === "all" && last ? t("first_thought_end", { date: short(last.created_at) }) : undefined}
        empty={
          filter === "all" ? (
            <Empty title={t("empty_timeline")} body={t("empty_timeline_body")}
              cta={t("write_first")} onCta={() => app.newThought()} />
          ) : (
            <Empty title={t("nothing_here")} body={t("no_match_filter")} cta={t("show_all")} onCta={() => setFilter("all")} />
          )
        }
      />
    </>
  );
}
