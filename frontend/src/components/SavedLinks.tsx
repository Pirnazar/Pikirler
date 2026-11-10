import { useEffect, useState } from "react";
import { errorMessage, get, post, type Thought } from "../api/client";
import type { components } from "../api/schema";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useI18n, type I18n } from "../i18n";
import { full, short } from "../lib/format";
import { I, Icon } from "../lib/icons";

export type SavedLink = components["schemas"]["LinkOut"];
type LinkDetail = components["schemas"]["LinkDetail"];
type SavedComment = components["schemas"]["SavedComment"];

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

const day = (iso?: string | null) => (iso ? short(iso) : "");


/** The thought's text without the bare links whose pages are shown as cards. */
export function textWithoutSavedLinks(content: string, links: SavedLink[]): string {
  let text = content;
  for (const l of links) {
    if (l.status !== "ready") continue;
    const lines = text.split("\n").filter((line) => line.trim() !== l.url);
    text = lines.join("\n");
    if (text.trimEnd().endsWith(l.url)) text = text.trimEnd().slice(0, -l.url.length);
  }
  return text.replace(/\n{3,}/g, "\n\n").trim();
}

// --- one reader for the whole app ---------------------------------------------------

let openId: string | null = null;
const listeners = new Set<(id: string | null) => void>();

export function openReader(id: string | null) {
  openId = id;
  listeners.forEach((fn) => fn(id));
}

/** Re-reads the thought every few seconds while one of its pages is being saved. */
function usePollWhilePending(thought: Thought) {
  const pending = (thought.links ?? []).some((l) => l.status === "pending");
  useEffect(() => {
    if (!pending) return;
    let tries = 0;
    const timer = window.setInterval(async () => {
      tries += 1;
      try {
        const next = await get<Thought>(`/thoughts/${thought.id}`);
        if (!(next.links ?? []).some((l) => l.status === "pending") || tries > 60) {
          window.clearInterval(timer);
          emit({ type: "updated", thought: next });
        }
      } catch {
        if (tries > 60) window.clearInterval(timer);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pending, thought.id]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function linkError(t: I18n["t"], code?: string | null): string {
  switch (code) {
    case "link_not_found":
      return t("lk_not_found");
    case "link_blocked":
      return t("lk_blocked");
    case "link_unsupported":
      return t("lk_unsupported");
    case "link_too_large":
      return t("lk_too_large");
    case "link_dns":
    case "link_timeout":
    case "link_unreachable":
      return t("lk_unreachable");
    default:
      return t("lk_failed_generic");
  }
}

/** Cards for the pages a thought links to: saved copy, saving, or failed. */
export function SavedLinks({ thought, large }: { thought: Thought; large?: boolean }) {
  const app = useApp();
  const { t, tn } = useI18n();
  const links = thought.links ?? [];
  usePollWhilePending(thought);
  if (!links.length) return null;

  async function retry(e: React.MouseEvent, l: SavedLink) {
    e.stopPropagation();
    try {
      await post(`/links/${l.id}/refetch`);
      emit({ type: "updated", thought: { ...thought, links: links.map((x) => (x.id === l.id ? { ...x, status: "pending" as const, error_code: null } : x)) } });
    } catch (err) {
      app.toast(errorMessage(err));
    }
  }

  return (
    <div className={"pk-saved" + (large ? " large" : "")} onClick={(e) => e.stopPropagation()}>
      {links.map((l) =>
        l.status === "ready" && l.kind === "post" ? (
          <QuoteCard key={l.id} l={l} />
        ) : l.status === "ready" ? (
          <button key={l.id} type="button" className="pk-saved-card" onClick={() => openReader(l.id)} aria-label={t("lk_read", { title: l.title ?? host(l.url) })}>
            <span className="pk-saved-body">
              <span className="pk-saved-site">
                {[l.site_name || host(l.url), byline(l), day(l.published_at)].filter(Boolean).join(" · ")}
              </span>
              <span className="pk-saved-title">{l.title || host(l.url)}</span>
              {l.excerpt && <span className="pk-saved-excerpt">{l.excerpt}</span>}
              <span className="pk-saved-foot">
                <Icon d={I.bookmark} size="xs" /> {t("lk_saved")}
                {l.reading_minutes ? " · " + t("lk_minutes", { n: l.reading_minutes }) : ""}
                {l.comment_count ? " · " + tn("lk_comments", l.comment_count) : ""}
              </span>
            </span>
            {l.cover_url && <img className="pk-saved-cover" src={l.cover_url} alt="" loading="lazy" />}
          </button>
        ) : l.status === "pending" ? (
          <div key={l.id} className="pk-saved-card pending" aria-live="polite">
            <span className="pk-saved-body">
              <span className="pk-saved-site">{host(l.url)}</span>
              <span className="pk-saved-title">{t("lk_saving")}</span>
              <span className="pk-saved-shimmer" />
            </span>
          </div>
        ) : (
          <div key={l.id} className="pk-saved-card failed">
            <span className="pk-saved-body">
              <span className="pk-saved-site">{host(l.url)}</span>
              <span className="pk-saved-title">{t("lk_failed")}</span>
              <span className="pk-saved-excerpt">{linkError(t, l.error_code)}</span>
              <span className="pk-saved-foot">
                {l.error_code !== "link_blocked" && <button type="button" className="pk-link-btn" onClick={(e) => retry(e, l)}>{t("cp_retry")}</button>}
                <a href={l.url} target="_blank" rel="noopener noreferrer">{t("lk_open_original")} ↗</a>
              </span>
            </span>
          </div>
        ),
      )}
    </div>
  );
}

function byline(l: SavedLink): string {
  if (!l.author) return l.author_username ? "@" + l.author_username : "";
  return l.author_username && l.author_username !== l.author ? `${l.author} @${l.author_username}` : l.author;
}

/** A short post (a Teswirler pikir) quoted as it was written. */
function QuoteCard({ l }: { l: SavedLink }) {
  const { t, tn } = useI18n();
  return (
    <blockquote className="pk-quote" role="button" tabIndex={0} aria-label={t("lk_read", { title: l.author ?? host(l.url) })}
      onClick={() => openReader(l.id)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), openReader(l.id))}>
      <div className="pk-quote-head">
        <span className="pk-quote-avatar" aria-hidden="true">{(l.author || l.author_username || "?").slice(0, 1).toUpperCase()}</span>
        <span className="pk-quote-who">
          <strong>{l.author || l.author_username}</strong>
          {l.author_username && (
            <a href={l.author_url ?? undefined} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>@{l.author_username}</a>
          )}
        </span>
        <span className="pk-quote-site">{l.site_name || host(l.url)}</span>
      </div>
      <p className="pk-quote-text">{l.post_text}</p>
      {l.cover_url && <img className="pk-quote-img" src={l.cover_url} alt="" loading="lazy" />}
      <div className="pk-quote-foot">
        {l.published_at && <time dateTime={l.published_at}>{full(l.published_at)}</time>}
        {l.edited_at && <span>· {t("lk_edited")}</span>}
        {l.comment_count ? <span>· {tn("lk_comments", l.comment_count)}</span> : null}
        <span style={{ flex: 1 }} />
        <a href={l.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>{t("lk_open_original")} ↗</a>
      </div>
    </blockquote>
  );
}

function Comments({ items, depth = 0 }: { items: SavedComment[]; depth?: number }) {
  const { t } = useI18n();
  return (
    <ul className={"pk-comments" + (depth ? " replies" : "")}>
      {items.map((c, i) => (
        <li key={c.id ?? i} className="pk-comment">
          <div className="pk-comment-head">
            <strong>{c.author || c.username || "?"}</strong>
            {c.username && <a href={c.author_url ?? undefined} target="_blank" rel="noopener noreferrer">@{c.username}</a>}
            {c.created_at && <time dateTime={c.created_at}>{full(c.created_at)}</time>}
            {c.likes ? <span className="pk-comment-likes" title={t("lk_likes")}>♥ {c.likes}</span> : null}
          </div>
          {c.quote && <blockquote className="pk-comment-quote">{c.quote}</blockquote>}
          <p className="pk-comment-text">{c.text}</p>
          {c.replies && c.replies.length > 0 && <Comments items={c.replies} depth={depth + 1} />}
        </li>
      ))}
    </ul>
  );
}

/** Reading view of a saved page, mounted once in the shell. */
export function Reader() {
  const app = useApp();
  const { t } = useI18n();
  const [id, setId] = useState(openId);
  const [page, setPage] = useState<LinkDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    listeners.add(setId);
    return () => {
      listeners.delete(setId);
    };
  }, []);

  useEffect(() => {
    setPage(null);
    setError("");
    if (!id) return;
    get<LinkDetail>(`/links/${id}`).then(setPage, (e) => setError(errorMessage(e)));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && openReader(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id]);

  if (!id) return null;

  async function refresh() {
    if (!page) return;
    try {
      await post(`/links/${page.id}/refetch`);
      app.toast(t("lk_refreshing"));
      openReader(null);
    } catch (e) {
      app.toast(errorMessage(e));
    }
  }

  const paragraphs = (page?.text ?? "").split(/\n{2,}/).filter((p) => p.trim());
  const source = page ? page.final_url || page.url : "";

  return (
    <div className="dialog-backdrop" onClick={() => openReader(null)}>
      <article role="dialog" aria-modal="true" aria-label={page?.title ?? t("lk_reader")} className="pk-reader" onClick={(e) => e.stopPropagation()}>
        <div className="pk-reader-bar">
          <span className="pk-saved-site">{page ? page.site_name || host(page.url) : ""}</span>
          <span style={{ flex: 1 }} />
          {page && <a className="btn btn-secondary" href={source} target="_blank" rel="noopener noreferrer">{t("lk_open_original")} ↗</a>}
          <button className="pk-x-btn" aria-label={t("lk_close")} onClick={() => openReader(null)}><Icon d={I.x} /></button>
        </div>
        <div className="pk-reader-scroll">
          {error && <div className="pk-error" role="alert">{error}</div>}
          {!page && !error && <div className="pk-loading-line">{t("looking")}</div>}
          {page && (
            <>
              {page.kind !== "post" && <h1 className="pk-reader-title">{page.title || host(page.url)}</h1>}
              <div className={"pk-reader-meta" + (page.kind === "post" ? " post" : "")}>
                {(page.author || page.author_username) && (
                  <span className="pk-reader-author">
                    <strong>{page.author || page.author_username}</strong>
                    {page.author_username && <a href={page.author_url ?? undefined} target="_blank" rel="noopener noreferrer">@{page.author_username}</a>}
                  </span>
                )}
                {page.published_at && <span>{t("lk_published", { date: full(page.published_at) })}</span>}
                {page.edited_at && <span>{t("lk_edited_on", { date: full(page.edited_at) })}</span>}
                {page.reading_minutes && page.kind !== "post" ? <span>{t("lk_minutes", { n: page.reading_minutes })}</span> : null}
              </div>
              {page.kind !== "post" && page.cover_url && <img className="pk-reader-cover" src={page.cover_url} alt="" />}
              {paragraphs.length ? (
                <div className={"pk-reader-text" + (page.kind === "post" ? " post" : "")}>{paragraphs.map((p, i) => <p key={i}>{p}</p>)}</div>
              ) : (
                <p className="pk-muted">{page.excerpt || t("lk_no_text")}</p>
              )}
              {page.kind === "post" && page.cover_url && <img className="pk-reader-cover" src={page.cover_url} alt="" />}
              {page.comment_count !== null && page.comment_count !== undefined && (
                <section className="pk-reader-comments" aria-label={t("lk_comments_title")}>
                  <h2>{t("lk_comments_title")} <span className="pk-muted">{page.comment_count}</span></h2>
                  {page.comments?.length ? <Comments items={page.comments} /> : <p className="pk-muted">{t("lk_no_comments")}</p>}
                </section>
              )}
              <div className="pk-reader-foot">
                <span>{t("lk_saved_on", { date: page.fetched_at ? full(page.fetched_at) : "" })}</span>
                <button className="pk-link-btn" onClick={refresh}>{t("lk_refresh")}</button>
              </div>
            </>
          )}
        </div>
      </article>
    </div>
  );
}
