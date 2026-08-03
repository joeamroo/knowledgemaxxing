import { useQuery } from "@tanstack/react-query";
import { fetchStats } from "./api";
import { CATEGORY_LABELS, SOURCE_LABELS, catStyle } from "./categories";

export function StatsPage() {
  const { data } = useQuery({ queryKey: ["stats"], queryFn: fetchStats });
  if (!data) {
    return (
      <div className="p-8 text-[13px]" style={{ color: "var(--ink-faint)" }}>
        counting the collection...
      </div>
    );
  }

  const months: { month: string; count: number }[] = data.items_per_month ?? [];
  const max = Math.max(1, ...months.map((m) => m.count));

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-7">
      <div className="reveal mb-8 flex flex-wrap items-end gap-10">
        <div>
          <div className="stat-number text-[52px] leading-none" style={{ color: "var(--accent)" }}>
            {data.total_items?.toLocaleString()}
          </div>
          <div className="smallcaps mt-1.5">items in the archive</div>
        </div>
        <div>
          <div className="stat-number text-[32px] leading-none">{data.essays?.toLocaleString()}</div>
          <div className="smallcaps mt-1.5">essays</div>
        </div>
        <div>
          <div className="stat-number text-[32px] leading-none">
            {Object.values(data.categories ?? {}).reduce((a: number, b) => a + (b as number), 0).toLocaleString()}
          </div>
          <div className="smallcaps mt-1.5">classified tweets</div>
        </div>
        {data.streaks?.longest > 0 && (
          <div title={data.streaks.longest_span ?? ""}>
            <div className="stat-number text-[32px] leading-none">
              {data.streaks.longest.toLocaleString()}
            </div>
            <div className="smallcaps mt-1.5">day longest streak</div>
          </div>
        )}
        {data.streaks?.active_days > 0 && (
          <div title={`${data.streaks.first_day ?? ""} to ${data.streaks.last_day ?? ""}`}>
            <div className="stat-number text-[32px] leading-none">
              {data.streaks.active_days.toLocaleString()}
            </div>
            <div className="smallcaps mt-1.5">days with traces</div>
          </div>
        )}
      </div>

      <section className="reveal mb-8" style={{ animationDelay: "80ms" }}>
        <div className="smallcaps mb-2">accumulation, by month</div>
        <div className="flex h-28 items-end gap-px rounded-md border hairline p-2"
          style={{ background: "var(--bg-raised)" }}>
          {months.map((m) => (
            <div key={m.month} title={`${m.month}: ${m.count.toLocaleString()}`}
              className="stat-bar flex-1"
              style={{ height: `${Math.max(2, (m.count / max) * 100)}%` }} />
          ))}
        </div>
        {months.length > 0 && (
          <div className="font-mono-data mt-1 flex justify-between" style={{ color: "var(--ink-faint)" }}>
            <span>{months[0].month}</span>
            <span>{months[months.length - 1].month}</span>
          </div>
        )}
      </section>

      <section className="reveal mb-8" style={{ animationDelay: "110ms" }}>
        <div className="smallcaps mb-2">the last year, day by day</div>
        <Heatmap days={data.items_per_day ?? []} />
      </section>

      {(data.by_hour ?? []).some((c: number) => c > 0) && (
        <section className="reveal mb-8" style={{ animationDelay: "125ms" }}>
          <div className="smallcaps mb-2">hour of day, local time</div>
          <div className="flex h-24 items-end gap-[3px] rounded-md border hairline p-2"
            style={{ background: "var(--bg-raised)" }}>
            {(data.by_hour as number[]).map((count, hour) => (
              <div key={hour}
                title={`${String(hour).padStart(2, "0")}:00 · ${count.toLocaleString()} traces`}
                className="stat-bar flex-1"
                style={{
                  height: `${Math.max(2, (count / Math.max(1, ...data.by_hour)) * 100)}%`,
                }} />
            ))}
          </div>
          <div className="font-mono-data mt-1 flex justify-between" style={{ color: "var(--ink-faint)" }}>
            <span>midnight</span>
            <span>6am</span>
            <span>noon</span>
            <span>6pm</span>
            <span>11pm</span>
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <section className="reveal" style={{ animationDelay: "140ms" }}>
          <div className="smallcaps mb-2">by source</div>
          <BarList entries={Object.entries(data.by_source_kind ?? {}).map(
            ([k, v]) => [SOURCE_LABELS[k] ?? k, v as number]
          )} />
        </section>

        {Object.keys(data.categories ?? {}).length > 0 && (
          <section className="reveal" style={{ animationDelay: "200ms" }}>
            <div className="smallcaps mb-2">tweet categories</div>
            <div className="space-y-1">
              {Object.entries(data.categories).map(([name, count]) => (
                <div key={name} className="flex items-center gap-2 text-[12.5px]">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ ...catStyle(name), background: "var(--cat)" }} />
                  <span className="w-28 truncate">{CATEGORY_LABELS[name] ?? name}</span>
                  <CatBar count={count as number}
                    max={Math.max(...Object.values(data.categories).map((v) => v as number))}
                    style={catStyle(name)} />
                  <span className="font-mono-data" style={{ color: "var(--ink-faint)" }}>
                    {(count as number).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="reveal lg:col-span-2" style={{ animationDelay: "260ms" }}>
          <div className="smallcaps mb-2">most-visited domains</div>
          <BarList entries={(data.top_domains ?? []).map(
            ([d, c]: [string, number]) => [d, c] as [string, number]
          )} mono />
        </section>

        {(data.igniting ?? []).length > 0 && (
          <section className="reveal" style={{ animationDelay: "290ms" }}>
            <div className="smallcaps mb-2">swarming right now</div>
            <div className="space-y-1">
              {data.igniting.map((t: { domain: string; recent: number; per_day: number }) => (
                <div key={t.domain} className="flex items-baseline gap-2 text-[12.5px]">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full animate-pulse"
                    style={{ background: "var(--accent)" }} />
                  <span className="font-mono-data truncate">{t.domain}</span>
                  <span className="font-mono-data ml-auto" style={{ color: "var(--ink-faint)" }}>
                    {t.recent} hits · {t.per_day}/day
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        <RabbitHoles />
        <div className="lg:col-span-2 grid grid-cols-1 gap-8 lg:grid-cols-2">
          <CoverageCard />
          <EgressCard />
        </div>
      </div>
    </div>
  );
}

function RabbitHoles() {
  const { data } = useQuery({
    queryKey: ["episodes"],
    queryFn: () => fetch("/api/episodes?days=30").then((r) => r.json()) as Promise<{
      episodes: { start: string; visits: number; top_domains: Record<string, number>; sample_titles: string[] }[];
    }>,
    staleTime: 300_000,
  });
  const eps = data?.episodes ?? [];
  if (!eps.length) return null;
  return (
    <section className="reveal" style={{ animationDelay: "320ms" }}>
      <div className="smallcaps mb-2">rabbit holes, last 30 days</div>
      <div className="space-y-1.5">
        {eps.slice(0, 5).map((e, i) => (
          <div key={i} className="text-[12.5px]" title={e.sample_titles.join("\n")}>
            <span className="font-mono-data" style={{ color: "var(--ink-faint)" }}>
              {e.start.replace("T", " ")} ·{" "}
            </span>
            {e.visits} visits deep into {Object.keys(e.top_domains).slice(0, 2).join(", ")}
          </div>
        ))}
      </div>
    </section>
  );
}

function CoverageCard() {
  const { data } = useQuery({
    queryKey: ["coverage"],
    queryFn: () => fetch("/api/coverage").then((r) => r.json()),
    staleTime: 300_000,
  });
  if (!data) return null;
  return (
    <section className="reveal" style={{ animationDelay: "350ms" }}>
      <div className="smallcaps mb-2">index coverage</div>
      <div className="text-[12.5px]" style={{ color: data.healthy ? "var(--ink-dim)" : "#c96b5a" }}>
        {data.totals.chunks.toLocaleString()} chunks over {data.totals.items.toLocaleString()} items
        {data.healthy ? " · search sees everything" : " · gaps found, run a sync"}
      </div>
      {(data.dead_saves ?? []).length > 0 && (
        <div className="mt-1.5 text-[12px]" style={{ color: "var(--ink-faint)" }}
          title={data.dead_saves.map((d: { url: string }) => d.url).join("\n")}>
          {data.dead_saves.length}+ saved links have gone dead (text preserved where fetched)
        </div>
      )}
    </section>
  );
}

function EgressCard() {
  const { data } = useQuery({
    queryKey: ["egress"],
    queryFn: () => fetch("/api/egress").then((r) => r.json()),
    staleTime: 60_000,
  });
  if (!data) return null;
  const channels = Object.entries(data.by_channel ?? {});
  return (
    <section className="reveal" style={{ animationDelay: "380ms" }}>
      <div className="smallcaps mb-2">what has left this machine</div>
      {channels.length === 0 ? (
        <div className="text-[12.5px]" style={{ color: "var(--ink-dim)" }}>
          Nothing has ever left the archive.
        </div>
      ) : (
        <div className="space-y-1">
          {channels.map(([channel, s]: [string, { events: number; items: number }]) => (
            <div key={channel} className="flex items-baseline gap-2 text-[12.5px]">
              <span className="font-mono-data">{channel}</span>
              <span className="font-mono-data ml-auto" style={{ color: "var(--ink-faint)" }}>
                {s.items.toLocaleString()} items · {s.events.toLocaleString()} events
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Heatmap({ days }: { days: { day: string; count: number }[] }) {
  const byDay = new Map(days.map((d) => [d.day, d.count]));
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 364);
  // align to the Sunday on or before start
  start.setDate(start.getDate() - start.getDay());
  const weeks: { day: string; count: number }[][] = [];
  const cursor = new Date(start);
  while (cursor <= today) {
    const week: { day: string; count: number }[] = [];
    for (let i = 0; i < 7 && cursor <= today; i++) {
      const key = cursor.toISOString().slice(0, 10);
      week.push({ day: key, count: byDay.get(key) ?? 0 });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }
  const max = Math.max(1, ...days.map((d) => d.count));
  const level = (count: number) =>
    count === 0 ? 0 : Math.min(4, Math.ceil((count / max) * 4));
  const opacities = [0.06, 0.25, 0.45, 0.7, 1];
  return (
    <div className="flex gap-[3px] overflow-x-auto rounded-md border hairline p-3"
      style={{ background: "var(--bg-raised)" }}>
      {weeks.map((week, wi) => (
        <div key={wi} className="flex flex-col gap-[3px]">
          {week.map((cell) => (
            <div key={cell.day} title={`${cell.day}: ${cell.count.toLocaleString()} items`}
              className="h-[11px] w-[11px] rounded-[2px]"
              style={{
                background: cell.count === 0 ? "var(--bg-inset)" : "var(--accent)",
                opacity: cell.count === 0 ? 1 : opacities[level(cell.count)],
              }} />
          ))}
        </div>
      ))}
    </div>
  );
}

function BarList({ entries, mono }: { entries: [string, number][]; mono?: boolean }) {
  const max = Math.max(1, ...entries.map(([, c]) => c));
  return (
    <div className="space-y-1">
      {entries.map(([name, count]) => (
        <div key={name} className="flex items-center gap-2 text-[12.5px]">
          <span className={`w-44 truncate ${mono ? "font-mono-data" : ""}`}>{name}</span>
          <div className="h-2.5 flex-1">
            <div className="stat-bar h-full" style={{ width: `${(count / max) * 100}%` }} />
          </div>
          <span className="font-mono-data w-16 text-right" style={{ color: "var(--ink-faint)" }}>
            {count.toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  );
}

function CatBar({ count, max, style }: { count: number; max: number; style: React.CSSProperties }) {
  return (
    <div className="h-2 flex-1">
      <div className="h-full rounded-sm"
        style={{ ...style, width: `${(count / max) * 100}%`, background: "var(--cat)", opacity: 0.75 }} />
    </div>
  );
}
