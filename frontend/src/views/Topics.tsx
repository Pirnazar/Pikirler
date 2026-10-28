import { useState } from "react";
import { get, type Page, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { usePaged } from "../app/hooks";
import { Empty, Feed } from "../components/Feed";
import { Header } from "../components/Header";
import { plural } from "../lib/format";

const norm = (s: string) => s.normalize("NFKC").toLocaleLowerCase();

export function TopicsView({ initialTag }: { initialTag?: string }) {
  const app = useApp();
  const [sel, setSel] = useState<string[]>(initialTag ? [initialTag] : []);
  const toggle = (name: string) => setSel((s) => (s.some((x) => norm(x) === norm(name)) ? s.filter((x) => norm(x) !== norm(name)) : [...s, name]));

  return (
    <>
      <Header title="Topics" sub="Pick several to narrow down" />
      <div className="pk-ruled">
        {app.tags.length ? (
          <div className="pk-grid pk-cols-3" style={{ borderBottom: "2px solid var(--color-divider)" }}>
            {app.tags.map((t) => {
              const on = sel.some((x) => norm(x) === t.normalized_name || norm(x) === norm(t.name));
              return (
                <button key={t.id} className="pk-cell pk-topic" aria-pressed={on} onClick={() => toggle(t.name)}>
                  <span className="t">#{t.name}</span>
                  <span className="pk-muted" style={{ fontSize: 12 }}>{plural(t.usage_count ?? 0, "thought")}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <Empty title="No topics yet." body="Write #something in a thought and it becomes a topic here." />
        )}
      </div>
      {sel.length > 0 ? <TopicList key={sel.join("|")} tags={sel} onClear={() => setSel([])} /> : app.tags.length > 0 && (
        <Empty title="Pick a topic above." body="Select one or several — thoughts must match all of them." />
      )}
    </>
  );
}

function TopicList({ tags, onClear }: { tags: string[]; onClear: () => void }) {
  const [first, ...rest] = tags;
  const keep = (t: Thought) => rest.every((g) => t.tag_refs.some((r) => r.normalized_name === norm(g) || norm(r.name) === norm(g)));
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/thoughts", { tag: first, limit: 100, cursor, status: "published" }, signal), [], { keep });
  return (
    <Feed {...feed} heading={`${plural(feed.items?.length ?? 0, "thought")}${feed.hasMore ? "+" : ""} · #${tags.join(" + #")}`}
      empty={<Empty title="No thought has all of these topics." body="Try removing one of the selected topics." cta="Clear selection" onCta={onClear} />} />
  );
}
