import { useEffect, useState } from "react";
import { del, errorMessage, get, post, type Page, type Relation, type SearchResponse, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { short, snip } from "../lib/format";

export function ConfirmDelete({ snippet, onConfirm, onCancel }: { snippet: string; onConfirm: () => void; onCancel: () => void }) {
  useEffect(() => {
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onCancel]);
  return (
    <div className="dialog-backdrop" style={{ zIndex: 55 }} onClick={onCancel}>
      <div role="alertdialog" aria-modal="true" aria-label="Delete thought" className="dialog" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-title">Delete this thought?</div>
        <p style={{ fontSize: 14, color: "var(--color-neutral-700)", margin: 0 }}>
          “{snippet}” — it goes to the trash; you can undo right away or restore it from My Thoughts for 30 days.
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button className="btn btn-primary" onClick={onConfirm} autoFocus>Delete</button>
          <button className="btn btn-secondary" onClick={onCancel}>Keep it</button>
        </div>
      </div>
    </div>
  );
}

interface Candidate {
  id: string;
  created_at: string;
  text: string;
}

/** Manual relations between thoughts. Owner-made links are confirmed at once;
 *  AI suggestions live elsewhere and never become links without a click. */
export function ConnectDialog({ thought, onClose }: { thought: Thought; onClose: () => void }) {
  const app = useApp();
  const [q, setQ] = useState("");
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [rels, setRels] = useState<Relation[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    get<Relation[]>(`/thoughts/${thought.id}/relations`).then(setRels, () => {});
  }, [thought.id]);

  useEffect(() => {
    const ac = new AbortController();
    const t = setTimeout(async () => {
      try {
        if (q.trim()) {
          const r = await get<SearchResponse>("/search", { q: q.trim(), limit: 12 }, ac.signal);
          setCands(r.results.map((x) => ({ id: x.thought_id, created_at: x.created_at, text: x.snippet })));
        } else {
          const p = await get<Page<Thought>>("/thoughts", { limit: 12 }, ac.signal);
          setCands(p.items.map((x) => ({ id: x.id, created_at: x.created_at, text: x.content || "Voice note" })));
        }
      } catch {
        if (!ac.signal.aborted) setCands([]);
      }
    }, q ? 250 : 0);
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [q]);

  useEffect(() => {
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);

  const linkOf = (id: string) =>
    rels.find((r) => r.status !== "rejected" && (r.source_thought_id === id || r.target_thought_id === id) && r.status === "confirmed");

  async function toggle(id: string) {
    setBusy(id);
    try {
      const existing = linkOf(id);
      if (existing) {
        await del(`/relations/${existing.id}`);
        setRels((list) => list.filter((r) => r.id !== existing.id));
      } else {
        const r = await post<Relation>(`/thoughts/${thought.id}/relations`, { target_thought_id: id, relation_type: "related" });
        setRels((list) => [...list, r]);
      }
    } catch (e) {
      app.toast(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Connect thoughts" className="dialog"
        style={{ maxWidth: 520, maxHeight: "80%" }} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-title">Connect thoughts</div>
        <p style={{ fontSize: 13, color: "var(--color-neutral-700)", margin: 0 }}>“{snip(thought.content || "Voice note", 90)}”</p>
        <input className="input" type="search" placeholder="Find a thought…" aria-label="Find a thought" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <div style={{ overflowY: "auto" }}>
          {cands === null && <div className="pk-loading-line" style={{ padding: "10px 0" }}>Looking…</div>}
          {cands?.length === 0 && <div className="pk-muted" style={{ padding: "10px 0", fontSize: 14 }}>No thoughts found.</div>}
          {cands?.filter((c) => c.id !== thought.id).map((c) => {
            const on = !!linkOf(c.id);
            return (
              <div key={c.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--color-divider)" }}>
                <div style={{ flex: 1, minWidth: 0, fontSize: 14 }}>
                  <div className="pk-muted" style={{ fontSize: 12 }}>{short(c.created_at)}</div>
                  {snip(c.text, 90)}
                </div>
                <button disabled={busy === c.id} onClick={() => toggle(c.id)}
                  style={{
                    border: "1px solid var(--color-text)", background: on ? "var(--color-accent)" : "transparent",
                    color: on ? "var(--color-bg)" : "var(--color-text)", borderColor: on ? "var(--color-accent)" : "var(--color-text)",
                    padding: "5px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer", flex: "none",
                  }}>
                  {on ? "Connected" : "Connect"}
                </button>
              </div>
            );
          })}
        </div>
        <div><button className="btn btn-primary" onClick={onClose}>Done</button></div>
      </div>
    </div>
  );
}
