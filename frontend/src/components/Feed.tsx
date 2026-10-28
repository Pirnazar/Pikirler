import { useRef, type ReactNode } from "react";
import type { Thought } from "../api/client";
import { useThoughtEvents } from "../app/bus";
import { ThoughtCard } from "./ThoughtCard";

export function Skeletons({ n = 3 }: { n?: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="pk-skeleton" aria-hidden="true">
          <div style={{ width: 40, height: 40 }} />
          <div style={{ background: "none" }}>
            <div style={{ height: 12, width: "30%" }} />
            <div style={{ height: 14, width: "92%", marginTop: 12 }} />
            <div style={{ height: 14, width: "64%", marginTop: 8 }} />
          </div>
        </div>
      ))}
    </>
  );
}

export function Empty({ title, body, cta, onCta }: { title: string; body?: string; cta?: string; onCta?: () => void }) {
  return (
    <div className="pk-empty">
      <h3>{title}</h3>
      {body && <p>{body}</p>}
      {cta && <button className="btn btn-primary" style={{ marginTop: 6 }} onClick={onCta}>{cta}</button>}
    </div>
  );
}

/** A list of thought cards with loading, empty, error and "load more" states. */
export function Feed({ items, error, hasMore, loadingMore, more, heading, empty, endNote }: {
  items: Thought[] | null;
  error?: string;
  hasMore?: boolean;
  loadingMore?: boolean;
  more?: () => void;
  heading?: string;
  empty: ReactNode;
  endNote?: string;
}) {
  const fresh = useRef(new Set<string>());
  useThoughtEvents((e) => {
    if (e.type === "created") fresh.current.add(e.thought.id);
  });
  if (!items) return <Skeletons />;
  return (
    <>
      {error && <div className="pk-pad pk-error" role="alert">{error}</div>}
      {heading && items.length > 0 && <div className="pk-list-heading">{heading}</div>}
      {items.map((t) => <ThoughtCard key={t.id} t={t} fresh={fresh.current.has(t.id)} />)}
      {!items.length && !error && empty}
      {hasMore && (
        <div className="pk-loadmore">
          <button className="btn btn-secondary" disabled={loadingMore} onClick={more}>
            {loadingMore ? "Loading…" : "Show older thoughts"}
          </button>
        </div>
      )}
      {!hasMore && endNote && items.length > 0 && <div className="pk-endnote">{endNote}</div>}
    </>
  );
}
