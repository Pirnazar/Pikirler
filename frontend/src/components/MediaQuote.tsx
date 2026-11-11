import { useState } from "react";
import { errorMessage, post, type MediaQuoteSource } from "../api/client";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";

const words = {
  ru: { save: "В заметки", saved: "Открыть заметку", busy: "Сохраняю…", done: "Цитата сохранена", source: "Источник цитаты" },
  en: { save: "Save to notes", saved: "Open note", busy: "Saving…", done: "Quote saved", source: "Quote source" },
  tk: { save: "Bellige goş", saved: "Belligi aç", busy: "Saklanýar…", done: "Söz saklandy", source: "Sözüň çeşmesi" },
};

export function mediaClock(ms: number | null | undefined) {
  if (ms == null) return "";
  const s = Math.floor(ms / 1000);
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(v => String(v).padStart(2, "0")).join(":");
}

export function SaveQuoteButton({ source, reference, position }: {
  source: "cinema" | "passage" | "youtube_transcript" | "lyrics"; reference: string; position?: number;
}) {
  const app = useApp(); const { lang } = useI18n(); const c = words[lang];
  const [busy, setBusy] = useState(false);
  const [id, setId] = useState("");
  async function save() {
    setBusy(true);
    try {
      const result = await post<{ thought_id: string }>("/media-search/quotes", { source, reference, position });
      setId(result.thought_id); app.toast(c.done);
    } catch (e) { app.toast(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return id ? <a className="pk-media-save" href={`#/detail/${id}`} onClick={e => e.stopPropagation()}>{c.saved}</a>
    : <button type="button" className="pk-media-save" disabled={busy} onClick={e => { e.stopPropagation(); void save(); }}>{busy ? c.busy : c.save}</button>;
}

export function MediaCitation({ quote }: { quote?: MediaQuoteSource | null }) {
  const { lang } = useI18n(); const c = words[lang];
  if (!quote) return null;
  return <aside className="pk-media-citation" aria-label={c.source}>
    <a href={quote.url} target={quote.url.startsWith("https:") ? "_blank" : undefined} rel="noreferrer" onClick={e => e.stopPropagation()}>
      {quote.title}{quote.title_en && quote.title_en !== quote.title ? ` / ${quote.title_en}` : ""}
    </a>
    <span>{[quote.year, quote.language?.toUpperCase(), mediaClock(quote.start_ms)].filter(Boolean).join(" · ")}</span>
  </aside>;
}
