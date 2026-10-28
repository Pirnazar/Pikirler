import { useEffect, useState } from "react";
import { get, type Activity, type MonthlyRecap, type OnThisDay, type Page, type Thought } from "../api/client";
import type { components } from "../api/schema";
import { useApp } from "../app/context";
import { useLoad } from "../app/hooks";
import { Header } from "../components/Header";
import { useI18n } from "../i18n";
import { fmtDate, full, rel, short, snip } from "../lib/format";
import { I, Icon } from "../lib/icons";

type RandomMemory = components["schemas"]["RandomMemory"];
const pad = (n: number) => String(n).padStart(2, "0");

export function MemoriesView() {
  const app = useApp();
  const { t, tn } = useI18n();
  const now = new Date();
  const otd = useLoad((s) => get<OnThisDay>("/memories/on-this-day", undefined, s), []);
  const [random, setRandom] = useState<Thought | null>(null);
  const [randomNone, setRandomNone] = useState(false);
  const activity = useLoad(async (s) => {
    const y = now.getFullYear();
    const [a, b] = await Promise.all([get<Activity>("/memories/activity", { year: y - 1 }, s), get<Activity>("/memories/activity", { year: y }, s)]);
    return [...a.days, ...b.days];
  }, []);
  const recap = useLoad((s) => get<MonthlyRecap>("/memories/monthly", { year: now.getFullYear(), month: now.getMonth() + 1 }, s), []);

  async function nextRandom() {
    try {
      const r = await get<RandomMemory>("/memories/random");
      if (r.thought && r.thought.id === random?.id) {
        const again = await get<RandomMemory>("/memories/random");
        setRandom(again.thought ?? null);
      } else setRandom(r.thought ?? null);
      setRandomNone(!r.thought);
    } catch {
      setRandomNone(true);
    }
  }

  const months = Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - 11 + i, 1);
    const key = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
    const n = (activity.data ?? []).filter((x) => x.date.startsWith(key)).reduce((a, x) => a + x.count, 0);
    return { key, d, n };
  });
  const maxM = Math.max(1, ...months.map((m) => m.n));
  const groups = otd.data?.groups ?? [];
  const r = recap.data;
  const topTag = r?.top_tags[0];
  const busiest = r?.busiest_day;

  return (
    <>
      <Header title={t("nav_memories")} sub={t("memories_sub")}
        right={<button className="btn btn-secondary" style={{ minHeight: 38, gap: 8 }} onClick={nextRandom}><Icon d={I.shuffle} size="sm" />{t("random_memory")}</button>} />
      <section className="pk-ruled" style={{ padding: "24px 20px" }}>
        <div className="pk-section-label" style={{ color: "var(--color-accent-700)" }}>{t("on_this_day")}</div>
        {otd.loading && <div className="pk-loading-line" style={{ marginTop: 10 }}>{t("looking_back")}</div>}
        {groups.flatMap((g) => g.thoughts.map((th) => (
          <button key={th.id} className="pk-otd" onClick={() => app.go("detail", th.id)}>
            <span style={{ display: "block", fontSize: 14, color: "var(--color-neutral-700)" }}>
              {tn("years_ago_wrote", g.years_ago)}
            </span>
            <span className="q">“{snip(th.content || t("voice_note"), 400)}”</span>
            <span style={{ display: "block", fontSize: 12, color: "var(--color-neutral-600)", marginTop: 8 }}>{full(th.created_at)}</span>
          </button>
        )))}
        {!otd.loading && !groups.length && (
          <p style={{ margin: "10px 0 0", color: "var(--color-neutral-700)" }}>
            {t("no_otd")}
          </p>
        )}
      </section>
      {random && (
        <section className="pk-random">
          <div className="pk-section-label">{t("random_label", { date: full(random.created_at), ago: rel(random.created_at) })}</div>
          <p>{random.content || t("voice_note")}</p>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => app.go("detail", random.id)}>{t("open")}</button>
            <button className="link" onClick={nextRandom}>{t("another_one")}</button>
          </div>
        </section>
      )}
      {randomNone && !random && <p className="pk-pad pk-muted" style={{ margin: 0 }}>{t("no_random")}</p>}

      <section className="pk-pad pk-ruled">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <div className="pk-section-label">{t("timeline_12")}</div>
          <span className="pk-muted" style={{ fontSize: 12 }}>{t("tap_month")}</span>
        </div>
        <div className="pk-bars">
          {months.map((m) => {
            const label = fmtDate(m.d, { month: "long", year: "numeric" }) + ": " + m.n;
            return (
              <button key={m.key} title={label} aria-label={label} onClick={() => app.go("calendar", m.key)}>
                <span className="v">{m.n || ""}</span>
                <span className="b" style={{ height: Math.max(2, Math.round((m.n / maxM) * 120)) }} />
              </button>
            );
          })}
        </div>
        <div className="pk-bar-labels">{months.map((m) => <span key={m.key}>{fmtDate(m.d, { month: "narrow" })}</span>)}</div>
      </section>

      <Evolution />

      <section style={{ padding: "20px 20px 40px" }}>
        <div className="pk-section-label">{t("monthly_recap", { month: fmtDate(now, { month: "long", year: "numeric" }) })}</div>
        <div className="pk-grid pk-cols-4" style={{ marginTop: 12, border: "2px solid var(--color-divider)" }}>
          {([
            [t("stat_thoughts"), r ? String(r.total) : "…"],
            [t("ideas"), r ? String(r.by_type?.idea ?? 0) : "…"],
            [t("most_topic"), topTag ? "#" + topTag.name : "—"],
            [t("most_day"), busiest ? fmtDate(new Date(busiest.date + "T12:00:00"), { weekday: "short", month: "short", day: "numeric" }) : "—"],
          ] as const).map(([l, n]) => (
            <div key={l} style={{ background: "var(--color-bg)", padding: 14 }}>
              <div style={{ fontSize: 22, fontWeight: 800, overflowWrap: "anywhere" }}>{n}</div>
              <div className="pk-muted" style={{ fontSize: 12 }}>{l}</div>
            </div>
          ))}
        </div>
        {r?.highlights[0] && (
          <button onClick={() => app.go("detail", r.highlights[0].id)}
            style={{ display: "block", width: "100%", textAlign: "left", border: 0, background: "none", padding: "14px 0 0", cursor: "pointer" }}>
            <span className="pk-muted" style={{ display: "block", fontSize: 12 }}>{t("highlight", { date: short(r.highlights[0].created_at) })}</span>
            <span style={{ display: "block", fontSize: 16, lineHeight: 1.5, marginTop: 4 }}>{snip(r.highlights[0].content || t("voice_note"), 180)}</span>
          </button>
        )}
      </section>
    </>
  );
}

interface EvoOption {
  key: string;
  label: string;
  query: Record<string, string>;
}

/** How one idea developed: every thought of a project or topic, oldest first. */
function Evolution() {
  const app = useApp();
  const { t, tn } = useI18n();
  const options: EvoOption[] = [
    ...[...app.projects].sort((a, b) => (b.thought_count ?? 0) - (a.thought_count ?? 0)).slice(0, 3)
      .map((p) => ({ key: "p" + p.id, label: p.name, query: { project_id: p.id } })),
    ...app.tags.slice(0, 5).map((tg) => ({ key: "t" + tg.id, label: "#" + tg.name, query: { tag: tg.name } })),
  ].slice(0, 5);
  const [sel, setSel] = useState<string>("");
  const current = options.find((o) => o.key === sel) ?? options[0];
  const [items, setItems] = useState<Thought[] | null>(null);

  useEffect(() => {
    if (!current) return;
    const ac = new AbortController();
    setItems(null);
    get<Page<Thought>>("/thoughts", { ...current.query, limit: 100, status: "published" }, ac.signal).then(
      (p) => setItems([...p.items].reverse()),
      () => !ac.signal.aborted && setItems([]),
    );
    return () => ac.abort();
  }, [current?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!current) return null;
  const span = items && items.length > 1
    ? Math.round((new Date(items[items.length - 1].created_at).getTime() - new Date(items[0].created_at).getTime()) / 86400000) : 0;
  return (
    <section className="pk-pad pk-ruled">
      <div className="pk-section-label">{t("evolution")}</div>
      <div className="pk-chips">
        {options.map((o) => (
          <button key={o.key} className="pk-chip" style={{ padding: "5px 12px", fontSize: 13 }} aria-pressed={o.key === current.key} onClick={() => setSel(o.key)}>{o.label}</button>
        ))}
      </div>
      <div className="pk-muted" style={{ fontSize: 13, margin: "12px 0 10px" }}>
        {items === null ? "…" : items.length
          ? t("across", { count: tn("thoughts_n", items.length), span: span > 60 ? tn("months_n", Math.round(span / 30)) : tn("days_n", span) })
          : t("no_thoughts_yet")}
      </div>
      {items?.map((th, i) => {
        const gap = i ? Math.round((new Date(th.created_at).getTime() - new Date(items[i - 1].created_at).getTime()) / 86400000) : 0;
        return (
          <button key={th.id} className="pk-evo" onClick={() => app.go("detail", th.id)}>
            <span style={{ paddingBottom: 18 }}>
              <span style={{ display: "block", fontSize: 13, fontWeight: 800 }}>{short(th.created_at)}</span>
              <span className="pk-muted" style={{ fontSize: 11 }}>{i ? "+" + tn("days_n", gap) : t("first_mention")}</span>
            </span>
            <span className="rail"><span className="dot" /><span className="line" /></span>
            <span className="txt">{snip(th.content || t("voice_note"), 160)}</span>
          </button>
        );
      })}
    </section>
  );
}
