import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { errorMessage, post, type Attachment, type Thought } from "../api/client";
import { uploadFile } from "../api/upload";
import { emit } from "../app/bus";
import { useApp } from "../app/context";
import { useOnline } from "../app/hooks";
import { parseTags, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

const DRAFT_KEY = "pikirler.draft";

interface Pending {
  key: string;
  kind: "image" | "video" | "file" | "link";
  name: string;
  preview?: string;
  progress: number;
  att?: Attachment;
  error?: string;
  url?: string;
}

function readDraft(): string {
  try {
    return localStorage.getItem(DRAFT_KEY) || "";
  } catch {
    return "";
  }
}

/** The composer: inline on Home (desktop) or a full-screen sheet (mobile). The
 *  draft is kept on this device only, never sent anywhere until published. */
export function Composer({ variant, onClose }: { variant: "inline" | "sheet"; onClose?: () => void }) {
  const app = useApp();
  const online = useOnline();
  const [draft, setDraft] = useState(readDraft);
  const [status, setStatus] = useState(() => (readDraft() ? "Draft restored" : ""));
  const [focus, setFocus] = useState(!!readDraft());
  const [type, setType] = useState<"thought" | "idea" | "reflection">("thought");
  const [projectId, setProjectId] = useState("");
  const [items, setItems] = useState<Pending[]>([]);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkVal, setLinkVal] = useState("");
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const imageIn = useRef<HTMLInputElement>(null);
  const videoIn = useRef<HTMLInputElement>(null);
  const fileIn = useRef<HTMLInputElement>(null);
  const saveTimer = useRef<number>();
  const replyTo = app.composer.replyTo ?? null;

  // A "new thought" / "continue" request from anywhere focuses this composer.
  useEffect(() => {
    if (app.composer.projectId) setProjectId(app.composer.projectId);
    if (app.composer.focusToken || variant === "sheet") {
      setFocus(true);
      setTimeout(() => ta.current?.focus(), 40);
    }
  }, [app.composer.focusToken, app.composer.projectId, variant]);

  useEffect(() => () => items.forEach((p) => p.preview && URL.revokeObjectURL(p.preview)), []); // eslint-disable-line react-hooks/exhaustive-deps

  function changeDraft(v: string, el?: HTMLTextAreaElement | null) {
    if (el && variant === "inline") {
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    }
    setDraft(v);
    setStatus(v ? "Saving draft…" : "");
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      try {
        if (v) localStorage.setItem(DRAFT_KEY, v);
        else localStorage.removeItem(DRAFT_KEY);
      } catch {
        /* storage unavailable */
      }
      if (v) setStatus("Draft saved on this device");
    }, 600);
  }

  const patch = (key: string, p: Partial<Pending>) => setItems((list) => list.map((x) => (x.key === key ? { ...x, ...p } : x)));

  function addFiles(kind: Pending["kind"], files: FileList | null) {
    for (const f of [...(files ?? [])]) {
      const key = Math.random().toString(36).slice(2);
      const preview = kind === "image" ? URL.createObjectURL(f) : undefined;
      setItems((list) => [...list, { key, kind, name: f.name, preview, progress: 0 }]);
      setFocus(true);
      uploadFile(f, (p) => patch(key, { progress: p })).then(
        (att) => patch(key, { att, progress: 1 }),
        (e) => patch(key, { error: errorMessage(e), progress: 1 }),
      );
    }
  }

  function addLink() {
    let v = linkVal.trim();
    if (!v) return;
    if (!/^https?:\/\//i.test(v)) v = "https://" + v;
    let name = v;
    try {
      name = new URL(v).hostname.replace(/^www\./, "");
    } catch {
      return app.toast("That doesn't look like a link");
    }
    setItems((list) => [...list, { key: "l" + Date.now(), kind: "link", name, url: v, progress: 1 }]);
    setLinkVal("");
    setLinkOpen(false);
  }

  function remove(p: Pending) {
    if (p.preview) URL.revokeObjectURL(p.preview);
    setItems((list) => list.filter((x) => x.key !== p.key));
  }

  const uploading = items.some((p) => !p.att && !p.error && p.kind !== "link");
  const failed = items.some((p) => p.error);
  const canPublish = !!(draft.trim() || items.length) && !uploading && !failed && !busy && online;

  async function publish() {
    if (!canPublish) return;
    const links = items.filter((p) => p.kind === "link").map((p) => p.url!);
    const content = [draft.trim(), ...links].filter(Boolean).join("\n\n");
    const body = {
      content,
      thought_type: type,
      project_ids: projectId ? [projectId] : [],
      attachment_ids: items.flatMap((p) => (p.att ? [p.att.id] : [])),
    };
    setBusy(true);
    try {
      const created = await post<Thought>(replyTo ? `/thoughts/${replyTo.id}/replies` : "/thoughts", body);
      window.clearTimeout(saveTimer.current);
      emit({ type: "created", thought: created });
      if (replyTo) emit({ type: "updated", thought: { ...replyTo, reply_count: (replyTo.reply_count ?? 0) + 1 } });
      items.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
      setItems([]);
      setDraft("");
      setStatus("");
      setFocus(false);
      setType("thought");
      setProjectId("");
      setLinkOpen(false);
      try {
        localStorage.removeItem(DRAFT_KEY);
      } catch {
        /* storage unavailable */
      }
      if (ta.current) ta.current.style.height = "";
      app.clearComposerPreset();
      if (created.tags.length) app.reloadMeta();
      app.toast(replyTo ? "Thought continued" : "Thought published");
      onClose?.();
    } catch (e) {
      app.toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      publish();
    } else if (e.key === "Escape" && !draft && !items.length) {
      setFocus(false);
      app.clearComposerPreset();
    }
  }

  function insertHash() {
    changeDraft(draft + (draft && !/\s$/.test(draft) ? " #" : "#"));
    setTimeout(() => ta.current?.focus(), 10);
  }

  const expanded = focus || !!draft || items.length > 0 || !!replyTo;
  const statusText = uploading ? "Uploading…" : failed ? "Remove the file that failed to upload" : !online ? "Offline — draft kept on this device" : status;
  const detected = parseTags(draft);

  const hidden = (
    <>
      <input ref={imageIn} type="file" accept="image/*" multiple hidden onChange={(e) => { addFiles("image", e.target.files); e.target.value = ""; }} />
      <input ref={videoIn} type="file" accept="video/*" hidden onChange={(e) => { addFiles("video", e.target.files); e.target.value = ""; }} />
      <input ref={fileIn} type="file" hidden onChange={(e) => { addFiles("file", e.target.files); e.target.value = ""; }} />
    </>
  );

  const attachments = items.length > 0 && (
    <div className="pk-attachments" style={variant === "sheet" ? { flexWrap: "nowrap", overflowX: "auto", padding: "0 16px 10px", margin: 0 } : undefined}>
      {items.map((p) => (
        <div key={p.key} className={"pk-attachment" + (p.error ? " failed" : "")} title={p.error || p.name}>
          {p.kind === "image" ? (
            <div className="pk-attachment-img">{p.preview && <img className="grayscale" src={p.preview} alt={p.name} />}</div>
          ) : (
            <div className="pk-attachment-file">
              <span className="pk-kind">{p.kind === "link" ? "Link" : p.kind === "video" ? "Video" : "File"}</span>
              <span>{p.name}</span>
            </div>
          )}
          {!p.att && !p.error && p.kind !== "link" && (
            <div className="pk-progress"><div style={{ width: Math.round(p.progress * 100) + "%" }} /></div>
          )}
          <button className="pk-remove" aria-label="Remove attachment" onClick={() => remove(p)}><Icon d={I.x} size="xs" /></button>
        </div>
      ))}
    </div>
  );

  const replyLine = replyTo && (
    <div className="pk-replying" style={variant === "sheet" ? { padding: "10px 16px 0", margin: 0 } : undefined}>
      <Icon d={I.reply} size="xs" className="accent-stroke" />
      <span>Continuing: <strong>{snip(replyTo.content || "Voice note", 70)}</strong></span>
      <button className="pk-x-btn" aria-label="Stop continuing" onClick={app.clearComposerPreset}><Icon d={I.x} size="xs" /></button>
    </div>
  );

  if (variant === "sheet") {
    return (
      <div role="dialog" aria-modal="true" aria-label="New thought" className="pk-sheet">
        <div className="pk-sheet-head">
          <button onClick={onClose} style={{ border: 0, background: "none", padding: "10px 4px", cursor: "pointer", fontSize: 15 }}>Cancel</button>
          <span style={{ flex: 1, textAlign: "center", fontSize: 12, color: "var(--color-neutral-600)" }}>{statusText}</span>
          <button className="btn btn-primary" style={{ minHeight: 40 }} disabled={!canPublish} onClick={publish}>Publish</button>
        </div>
        {replyLine}
        <textarea ref={ta} aria-label="What's on your mind?" placeholder="What's on your mind?" value={draft}
          onChange={(e) => changeDraft(e.target.value)} onKeyDown={onKey} />
        {attachments}
        <div className="pk-sheet-tools">
          <button className="pk-tool" aria-label="Photo" onClick={() => imageIn.current?.click()}><Icon d={I.image} /></button>
          <button className="pk-tool" aria-label="Hashtag" onClick={insertHash}><Icon d={I.hash} /></button>
          <button className="pk-tool" aria-label="File" onClick={() => fileIn.current?.click()}><Icon d={I.clip} /></button>
          <span style={{ flex: 1 }} />
          <button aria-label="Record voice thought" onClick={app.openRecorder}
            style={{ width: 56, height: 56, display: "grid", placeItems: "center", border: 0, background: "var(--color-accent)", color: "var(--color-bg)", cursor: "pointer" }}>
            <Icon d={I.mic} />
          </button>
        </div>
        {hidden}
      </div>
    );
  }

  return (
    <section aria-label="New thought" className={"pk-composer" + (expanded ? " expanded" : "")}>
      <div className="pk-avatar">{app.initial}</div>
      <div style={{ minWidth: 0 }}>
        {replyLine}
        <textarea ref={ta} rows={1} aria-label="What's on your mind?" placeholder="What's on your mind?" value={draft}
          onChange={(e) => changeDraft(e.target.value, e.target)} onFocus={() => setFocus(true)} onKeyDown={onKey} />
        {attachments}
        {linkOpen && (
          <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
            <input className="input" type="url" placeholder="Paste a link" aria-label="Link URL" value={linkVal} autoFocus
              onChange={(e) => setLinkVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addLink(); } }} />
            <button className="btn btn-secondary" onClick={addLink}>Add</button>
          </div>
        )}
        {expanded && (
          <div className="pk-options">
            <div role="group" aria-label="Category (optional)" className="pk-seg">
              {(["thought", "idea", "reflection"] as const).map((k) => (
                <button key={k} aria-pressed={type === k} onClick={() => setType(k)}>{k[0].toUpperCase() + k.slice(1)}</button>
              ))}
            </div>
            {app.projects.length > 0 && (
              <select className="pk-select" aria-label="Project (optional)" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">No project</option>
                {app.projects.filter((p) => p.status !== "archived").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            )}
            {detected.map((g) => <span key={g} className="tag tag-neutral">#{g}</span>)}
          </div>
        )}
        <div className="pk-toolbar">
          <button className="pk-tool" title="Image" aria-label="Attach image" onClick={() => imageIn.current?.click()}><Icon d={I.image} /></button>
          <button className="pk-tool" title="Video" aria-label="Attach video" onClick={() => videoIn.current?.click()}><Icon d={I.video} /></button>
          <button className="pk-tool" title="Voice thought" aria-label="Record voice thought" onClick={app.openRecorder}><Icon d={I.mic} /></button>
          <button className="pk-tool" title="File" aria-label="Attach file" onClick={() => fileIn.current?.click()}><Icon d={I.clip} /></button>
          <button className="pk-tool" title="Link" aria-label="Add link" onClick={() => { setLinkOpen((x) => !x); setFocus(true); }}><Icon d={I.chain} /></button>
          <button className="pk-tool" title="Hashtag" aria-label="Insert hashtag" onClick={insertHash}><Icon d={I.hash} /></button>
          <span aria-live="polite" className="pk-draft-status">{statusText}</span>
          <button className="btn btn-primary" style={{ minHeight: 36 }} disabled={!canPublish} onClick={publish} title="Publish (Ctrl + Enter)">
            {busy ? "Publishing…" : "Publish"}
          </button>
        </div>
        {hidden}
      </div>
    </section>
  );
}
