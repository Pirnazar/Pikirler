import { useState } from "react";
import { get, type Page, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { usePaged } from "../app/hooks";
import { Empty, Feed } from "../components/Feed";
import { Header } from "../components/Header";
import { useI18n } from "../i18n";

const norm = (s: string) => s.normalize("NFKC").toLocaleLowerCase();

export function TopicsView({ initialTag }: { initialTag?: string }) {
  const app = useApp();
  const { t: tr, tn } = useI18n();
  const [sel, setSel] = useState<string[]>(initialTag ? [initialTag] : []);
  const toggle = (name: string) => setSel((s) => (s.some((x) => norm(x) === norm(name)) ? s.filter((x) => norm(x) !== norm(name)) : [...s, name]));

  return (
    <>
      <Header title={tr("nav_topics")} sub={tr("topics_sub")} />
      <div className="pk-ruled">
        {app.tags.length ? (
          <div className="pk-grid pk-cols-3" style={{ borderBottom: "2px solid var(--color-divider)" }}>
            {app.tags.map((t) => {
              const on = sel.some((x) => norm(x) === t.normalized_name || norm(x) === norm(t.name));
              return (
                <button key={t.id} className="pk-cell pk-topic" aria-pressed={on} onClick={() => toggle(t.name)}>
                  <span className="t">#{t.name}</span>
                  <span className="pk-muted" style={{ fontSize: 12 }}>{tn("thoughts_n", t.usage_count ?? 0)}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <Empty title={tr("no_topics")} body={tr("no_topics_body")} />
        )}
      </div>
      {sel.length > 0 ? <TopicList key={sel.join("|")} tags={sel} onClear={() => setSel([])} /> : app.tags.length > 0 && (
        <Empty title={tr("pick_topic")} body={tr("pick_topic_body")} />
      )}
    </>
  );
}

function TopicList({ tags, onClear }: { tags: string[]; onClear: () => void }) {
  const { t: tr, tn } = useI18n();
  const [first, ...rest] = tags;
  const keep = (t: Thought) => rest.every((g) => t.tag_refs.some((r) => r.normalized_name === norm(g) || norm(r.name) === norm(g)));
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/thoughts", { tag: first, limit: 100, cursor, status: "published" }, signal), [], { keep });
  return (
    <Feed {...feed} heading={`${tn("thoughts_n", feed.items?.length ?? 0)}${feed.hasMore ? "+" : ""} · #${tags.join(" + #")}`}
      empty={<Empty title={tr("no_topic_match")} body={tr("no_topic_match_body")} cta={tr("clear_selection")} onCta={onClear} />} />
  );
}
