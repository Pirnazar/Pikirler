import { useMemo, useState } from "react";
import { get, type Activity, type Page, type Thought } from "../api/client";
import { useApp } from "../app/context";
import { useLoad, usePaged } from "../app/hooks";
import { Empty, Feed } from "../components/Feed";
import { Header } from "../components/Header";
import { dayKey, plural } from "../lib/format";
import { I, Icon } from "../lib/icons";

const ARCHIVE_YEARS = 6;

export function CalendarView({ month }: { month?: string }) {
  const app = useApp();
  const now = new Date();
  const initial = month && /^\d{4}-\d{2}$/.test(month) ? { y: +month.slice(0, 4), m: +month.slice(5, 7) - 1 } : { y: now.getFullYear(), m: now.getMonth() };
  const [cal, setCal] = useState(initial);
  const [day, setDay] = useState<string>(() => (month ? "" : dayKey(now)));

  const years = useLoad(async (s) => {
    const list = await Promise.all(
      Array.from({ length: ARCHIVE_YEARS }, (_, i) => get<Activity>("/memories/activity", { year: now.getFullYear() - i }, s)),
    );
    return list;
  }, []);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of years.data ?? []) for (const d of a.days) c[d.date] = d.count;
    return c;
  }, [years.data]);

  // Coming from "tap a month": open the first day with thoughts in it.
  const firstDayOf = (y: number, m: number) => {
    const prefix = `${y}-${String(m + 1).padStart(2, "0")}`;
    return Object.keys(counts).filter((k) => k.startsWith(prefix) && counts[k] > 0).sort()[0] ?? "";
  };
  const shownDay = day || firstDayOf(cal.y, cal.m);

  const first = new Date(cal.y, cal.m, 1);
  const startDow = (first.getDay() + 6) % 7;
  const days = new Date(cal.y, cal.m + 1, 0).getDate();
  const today = dayKey(now);
  const cells: ({ k: string; d: number; c: number } | null)[] = [];
  for (let i = 0; i < startDow; i++) cells.push(null);
  for (let d = 1; d <= days; d++) {
    const k = dayKey(new Date(cal.y, cal.m, d));
    cells.push({ k, d, c: counts[k] ?? 0 });
  }
  while (cells.length % 7) cells.push(null);

  const archive = (years.data ?? []).filter((a) => a.total > 0).map((a) => {
    const byMonth: number[] = Array(12).fill(0);
    for (const d of a.days) byMonth[+d.date.slice(5, 7) - 1] += d.count;
    return { year: a.year, total: a.total, months: byMonth.map((n, m) => ({ m, n })).filter((x) => x.n > 0).reverse() };
  });

  const move = (delta: number) => {
    const d = new Date(cal.y, cal.m + delta, 1);
    setCal({ y: d.getFullYear(), m: d.getMonth() });
    setDay("");
  };

  return (
    <>
      <Header title="Calendar" sub="Every day you wrote something" />
      <div className="pk-ruled">
        <div className="pk-cal-head">
          <div style={{ fontSize: 22, fontWeight: 800, flex: 1 }}>{first.toLocaleDateString("en-US", { month: "long", year: "numeric" })}</div>
          <button className="btn btn-ghost" style={{ minHeight: 34 }} onClick={() => { setCal({ y: now.getFullYear(), m: now.getMonth() }); setDay(today); }}>Today</button>
          <button className="pk-square-btn sm" onClick={() => move(-1)} aria-label="Previous month"><Icon d={I.left} size="sm" /></button>
          <button className="pk-square-btn sm" onClick={() => move(1)} aria-label="Next month"><Icon d={I.right} size="sm" /></button>
        </div>
        <div className="pk-grid pk-cal-grid">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((w) => <div key={w} className="pk-wd">{w}</div>)}
          {cells.map((c, i) =>
            c ? (
              <button key={c.k} aria-label={`${c.k}, ${plural(c.c, "thought")}`} onClick={() => setDay(c.k)}
                className={"pk-day" + (c.c >= 3 ? " l3" : c.c === 2 ? " l2" : c.c === 1 ? " l1" : "") + (c.k === today ? " today" : "") + (c.k === shownDay ? " sel" : "")}>
                <span className="n">{c.d}</span>
                <span className="c">{c.c ? <>{c.c}<span className="w"> {c.c === 1 ? "thought" : "thoughts"}</span></> : ""}</span>
              </button>
            ) : <button key={"b" + i} className="pk-day" disabled aria-hidden="true" tabIndex={-1} />,
          )}
        </div>
        {archive.length > 0 && (
          <div style={{ padding: "14px 20px" }}>
            <div className="pk-section-label" style={{ marginBottom: 8 }}>Archive</div>
            {archive.map((a) => (
              <div key={a.year} className="pk-archive-row">
                <div style={{ width: 96, flex: "none" }}>
                  <div style={{ fontWeight: 800 }}>{a.year}</div>
                  <div className="pk-muted" style={{ fontSize: 12 }}>{plural(a.total, "thought")}</div>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {a.months.map(({ m, n }) => (
                    <button key={m} className="pk-month-btn" aria-pressed={a.year === cal.y && m === cal.m}
                      onClick={() => { setCal({ y: a.year, m }); setDay(""); }}>
                      {new Date(a.year, m, 1).toLocaleDateString("en-US", { month: "short" })} <strong>{n}</strong>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {shownDay ? <DayList key={shownDay} day={shownDay} /> : (
        <Empty title="Nothing written this month." body="Pick a highlighted day to read what you wrote." cta="Write a thought" onCta={() => app.newThought()} />
      )}
    </>
  );
}

function DayList({ day }: { day: string }) {
  const [y, m, d] = day.split("-").map(Number);
  const from = new Date(y, m - 1, d), to = new Date(y, m - 1, d + 1);
  const feed = usePaged(
    (cursor, signal) => get<Page<Thought>>("/thoughts", { date_from: from.toISOString(), date_to: to.toISOString(), limit: 100, cursor }, signal),
    [day],
    { acceptsNew: (t) => t.created_at >= from.toISOString() && t.created_at < to.toISOString() },
  );
  const heading = from.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }) + " · " + plural(feed.items?.length ?? 0, "thought");
  return <Feed {...feed} heading={heading} empty={<Empty title="Nothing written this day." body="Pick a highlighted day to read what you wrote." />} />;
}
