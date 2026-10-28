import { useState, type FormEvent } from "react";
import { errorMessage, get, patch, post, type Page, type Project, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { useLoad, usePaged } from "../app/hooks";
import { Empty, Feed, Skeletons } from "../components/Feed";
import { Header } from "../components/Header";
import { plural, rel, short } from "../lib/format";
import { I, Icon } from "../lib/icons";

function ProjectForm({ project, onDone, onCancel }: { project?: Project; onDone: (p: Project) => void; onCancel: () => void }) {
  const app = useApp();
  const [name, setName] = useState(project?.name ?? "");
  const [desc, setDesc] = useState(project?.description ?? "");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const body = { name: name.trim(), description: desc.trim() || null };
      const p = project ? await patch<Project>(`/projects/${project.id}`, body) : await post<Project>("/projects", body);
      app.reloadMeta();
      onDone(p);
    } catch (err) {
      app.toast(errorMessage(err));
      setBusy(false);
    }
  }
  return (
    <div className="dialog-backdrop" onClick={onCancel}>
      <form role="dialog" aria-modal="true" aria-label={project ? "Edit project" : "New project"} className="dialog" style={{ maxWidth: 440 }}
        onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="dialog-title">{project ? "Edit project" : "New project"}</div>
        <div className="field"><label htmlFor="pj-name">Name</label>
          <input id="pj-name" className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus /></div>
        <div className="field"><label htmlFor="pj-desc">What it is (optional)</label>
          <textarea id="pj-desc" className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>{project ? "Save" : "Create project"}</button>
          <button className="btn btn-secondary" type="button" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </div>
  );
}

export function ProjectsView() {
  const app = useApp();
  const [creating, setCreating] = useState(false);
  const list = app.projects.filter((p) => p.status !== "archived");
  return (
    <>
      <Header title="Projects" sub="Thoughts grouped by what you are building"
        right={<button className="btn btn-secondary" style={{ minHeight: 38, gap: 8 }} onClick={() => setCreating(true)}><Icon d={I.plus} size="sm" />New project</button>} />
      {list.length ? (
        <div className="pk-grid pk-cols-2 stack pk-ruled">
          {list.map((p) => (
            <button key={p.id} className="pk-cell pk-project" onClick={() => app.go("project", p.id)}>
              <span className="sq" />
              <span className="n">{p.name}</span>
              <span className="d">{p.description || " "}</span>
              <span className="f"><strong>{plural(p.thought_count ?? 0, "thought")}</strong><span className="pk-muted">Updated {rel(p.updated_at)}</span></span>
            </button>
          ))}
        </div>
      ) : (
        <Empty title="No projects yet." body="A project groups thoughts about one thing you are building — and lets Jemşit find them later." cta="Create a project" onCta={() => setCreating(true)} />
      )}
      {creating && <ProjectForm onCancel={() => setCreating(false)} onDone={(p) => { setCreating(false); app.go("project", p.id); }} />}
    </>
  );
}

export function ProjectView({ id }: { id: string }) {
  const app = useApp();
  const [editing, setEditing] = useState(false);
  const proj = useLoad((s) => get<Project>(`/projects/${id}`, undefined, s), [id]);
  const feed = usePaged((cursor, signal) => get<Page<Thought>>("/thoughts", { project_id: id, limit: 100, cursor }, signal), [id], {
    acceptsNew: (t) => t.projects.some((p) => p.id === id),
  });
  const p = proj.data;
  if (proj.error) return (<><Header title="Project" back /><Empty title="This project isn't here." body={proj.error} /></>);
  if (!p) return (<><Header title="Project" back /><Skeletons n={2} /></>);
  const chrono = feed.items ? [...feed.items].reverse() : null;
  return (
    <>
      <Header title={p.name} sub="Project" back right={<button className="btn btn-secondary" onClick={() => setEditing(true)}>Edit</button>} />
      <div className="pk-pad pk-ruled">
        <div className="pk-display">{p.name}</div>
        {p.description && <p style={{ margin: "10px 0 0", maxWidth: 560, color: "var(--color-neutral-700)", textWrap: "pretty" } as React.CSSProperties}>{p.description}</p>}
        <div className="pk-stat-row pk-cols-3" style={{ display: "grid" }}>
          <div style={{ paddingTop: 10 }}><div className="n" style={{ fontSize: 24 }}>{p.thought_count ?? 0}</div><div className="l">Thoughts</div></div>
          <div style={{ paddingTop: 10 }}><div style={{ fontSize: 16, fontWeight: 800, paddingTop: 6 }}>{chrono?.[0] ? short(chrono[0].created_at) : "—"}</div><div className="l">First thought</div></div>
          <div style={{ paddingTop: 10 }}><div style={{ fontSize: 16, fontWeight: 800, paddingTop: 6 }}>{chrono?.length ? rel(chrono[chrono.length - 1].created_at) : "—"}</div><div className="l">Last activity</div></div>
        </div>
        <button className="btn btn-primary" style={{ marginTop: 16 }} onClick={() => app.newThought({ projectId: p.id })}>Add a thought to this project</button>
      </div>
      <Feed items={chrono} error={feed.error} hasMore={feed.hasMore} loadingMore={feed.loadingMore} more={feed.more}
        heading={feed.hasMore ? "Chronology · latest 100, oldest first" : "Chronology · oldest first"}
        empty={<Empty title="No thoughts in this project yet." />} />
      {editing && <ProjectForm project={p} onCancel={() => setEditing(false)} onDone={(np) => { setEditing(false); proj.setData(np); }} />}
    </>
  );
}
