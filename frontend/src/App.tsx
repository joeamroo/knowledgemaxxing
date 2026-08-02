import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchFacets, fetchItems, fetchMeta, fetchRandom, fetchStats, Item } from "./api";
import { CompanionPage } from "./Companion";
import { FeedPage } from "./Feed";
import { Onboarding } from "./Onboarding";
import { TasksPanel } from "./Tasks";
import { AskPanel } from "./Ask";
import { CATEGORY_LABELS, KIND_GLYPHS, KIND_LABELS, SOURCE_LABELS, catStyle } from "./categories";
import { Drawer } from "./Drawer";
import { StatsPage } from "./Stats";
import { TodayPanel } from "./Today";

function useUrlState() {
  const read = () => Object.fromEntries(new URLSearchParams(location.search));
  const [state, setState] = useState<Record<string, string>>(read);
  const update = useCallback((patch: Record<string, string | undefined>) => {
    setState((prev) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === "") delete next[k];
        else next[k] = v;
      }
      const qs = new URLSearchParams(next).toString();
      history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
      return next;
    });
  }, []);
  return [state, update] as const;
}

function useDebounced(value: string, ms = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

const isTweetKind = (k: string) =>
  ["like", "retweet", "own_tweet", "bookmark_tweet"].includes(k);

export default function App() {
  const [page, setPage] = useState<"table" | "stats" | "feed" | "companion">("table");
  const [params, setParams] = useUrlState();
  const [queryInput, setQueryInput] = useState(params.q ?? "");
  const query = useDebounced(queryInput);
  const [drawerItem, setDrawerItem] = useState<number | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const [todayOpen, setTodayOpen] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [focusRow, setFocusRow] = useState(0);
  const [dark, setDark] = useState(true);
  const searchRef = useRef<HTMLInputElement>(null);
  const metaQuery = useQuery({ queryKey: ["meta"], queryFn: fetchMeta, staleTime: Infinity });
  const readOnly = metaQuery.data?.read_only ?? false;

  const mode = params.mode ?? "hybrid";
  useEffect(() => setParams({ q: query || undefined }), [query]);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const filterParams = {
    kind: params.kind, source: params.source, category: params.category,
    domain: params.domain, date_from: params.date_from, date_to: params.date_to,
    starred: params.starred, is_essay: params.is_essay,
    in_reading_list: params.in_reading_list,
  };
  const activeFilters = Object.entries(filterParams).filter(([, v]) => v);

  const itemsQuery = useInfiniteQuery({
    queryKey: ["items", query, mode, filterParams, params.sort, params.order],
    queryFn: ({ pageParam }) =>
      fetchItems({
        q: query || undefined, mode: query ? mode : undefined,
        ...filterParams,
        sort: params.sort ?? "created_at", order: params.order ?? "desc",
        cursor: pageParam, page_size: 50,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const items = useMemo(
    () => itemsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [itemsQuery.data]
  );

  const facetsQuery = useQuery({
    queryKey: ["facets", filterParams],
    queryFn: () => fetchFacets(filterParams as Record<string, string | undefined>),
  });

  const [skipOnboarding, setSkipOnboarding] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [companionSeed, setCompanionSeed] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const statsQuery = useQuery({ queryKey: ["stats"], queryFn: fetchStats });
  const collectionsQuery = useQuery({
    queryKey: ["collections"],
    queryFn: () => fetch("/api/collections").then((r) => r.json()) as Promise<{
      collections: { id: number; name: string; spec: { query?: string; filters?: Record<string, string> } }[];
    }>,
  });

  const runSync = async () => {
    if (syncing) return;
    setSyncing(true);
    await fetch("/api/sync", { method: "POST" });
    const poll = setInterval(async () => {
      const s = await fetch("/api/sync/status").then((r) => r.json());
      if (!s.running) {
        clearInterval(poll);
        setSyncing(false);
        statsQuery.refetch();
        itemsQuery.refetch();
      }
    }, 2500);
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") {
        if (e.key === "Escape") target.blur();
        return;
      }
      if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); }
      else if (e.key === "j") setFocusRow((r) => Math.min(r + 1, items.length - 1));
      else if (e.key === "k") setFocusRow((r) => Math.max(r - 1, 0));
      else if (e.key === "Enter" && items[focusRow]) setDrawerItem(items[focusRow].id);
      else if (e.key === "s" && items[focusRow]) {
        import("./api").then(({ patchItem }) =>
          patchItem(items[focusRow].id, { starred: !items[focusRow].starred }).then(() =>
            itemsQuery.refetch()
          )
        );
      } else if (e.key === "a") { e.preventDefault(); setAskOpen(true); }
      else if (e.key === "Escape") {
        setDrawerItem(null); setAskOpen(false); setTasksOpen(false); setTodayOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [items, focusRow]);

  const toggleSelect = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const setFilter = (key: string, value?: string) =>
    setParams({ [key]: params[key] === value ? undefined : value });

  const sortBy = (col: string) =>
    setParams({
      sort: col,
      order: params.sort === col && params.order !== "asc" ? "asc" : "desc",
    });

  const sortGlyph = (col: string) =>
    params.sort === col ? (params.order === "asc" ? " ↑" : " ↓") : "";

  const firstRun = statsQuery.data && statsQuery.data.total_items === 0 && !skipOnboarding;
  if (firstRun || showOnboarding) {
    return (
      <Onboarding
        skipLabel={firstRun ? "skip, I'll use the CLI" : "back to the archive"}
        onDone={() => {
          statsQuery.refetch(); itemsQuery.refetch(); facetsQuery.refetch();
          setShowOnboarding(false);
        }}
        onSkip={() => { setSkipOnboarding(true); setShowOnboarding(false); }}
      />
    );
  }

  return (
    <div className="flex h-full">
      {/* ── card catalog ─────────────────────────────── */}
      <aside className="flex w-60 shrink-0 flex-col border-r hairline" style={{ background: "var(--bg-raised)" }}>
        <div className="px-4 pb-3 pt-4">
          <button className="brand text-[26px] leading-none" onClick={() => setPage("table")}>
            km<span style={{ color: "var(--accent)" }}>.</span>
          </button>
          <div className="smallcaps mt-1">knowledgemaxxing</div>
          {readOnly && (
            <div className="smallcaps mt-1" style={{ color: "var(--accent)" }}>read-only</div>
          )}
        </div>

        <div className="mb-1 flex gap-1 px-3">
          {(readOnly
            ? ([["table", "Archive"], ["feed", "Feed"]] as const)
            : ([["table", "Archive"], ["feed", "Feed"], ["companion", "Companion"]] as const)
          ).map(
            ([key, label]) => (
              <button key={key} onClick={() => setPage(key)}
                className="flex-1 rounded-md px-2 py-1.5 text-[12px] font-medium transition-colors"
                style={page === key
                  ? { background: "var(--accent)", color: "#1c1508" }
                  : { color: "var(--ink-dim)" }}>
                {label}
              </button>
            ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <Facet title="Collections">
            <FacetRow label="Everything" active={activeFilters.length === 0 && page === "table"}
              onClick={() => { setPage("table"); setParams(Object.fromEntries(activeFilters.map(([k]) => [k, undefined]))); }} />
            <FacetRow label="Essays" active={params.is_essay === "true"}
              onClick={() => { setPage("table"); setFilter("is_essay", "true"); }} />
            <FacetRow label="Reading lists" active={params.in_reading_list === "true"}
              onClick={() => { setPage("table"); setFilter("in_reading_list", "true"); }} />
            <FacetRow label="Starred" active={params.starred === "true"}
              onClick={() => { setPage("table"); setFilter("starred", "true"); }} />
            <FacetRow label="Aphorisms" dotStyle={catStyle("aphorism")}
              active={params.category === "aphorism"}
              onClick={() => { setPage("table"); setFilter("category", "aphorism"); }} />
            <FacetRow label="Natural laws" dotStyle={catStyle("natural_law")}
              active={params.category === "natural_law"}
              onClick={() => { setPage("table"); setFilter("category", "natural_law"); }} />
            <FacetRow label="Contrarian" dotStyle={catStyle("contrarian")}
              active={params.category === "contrarian"}
              onClick={() => { setPage("table"); setFilter("category", "contrarian"); }} />
            {(collectionsQuery.data?.collections ?? []).map((c) => (
              <FacetRow key={c.id} label={c.name}
                active={false}
                onClick={() => {
                  setPage("table");
                  const f = c.spec.filters ?? {};
                  setQueryInput(c.spec.query ?? "");
                  setParams({
                    q: c.spec.query || undefined,
                    kind: f.kind, domain: f.domain, category: f.category,
                    is_essay: f.is_essay ? "true" : undefined,
                    date_from: f.date_from, date_to: f.date_to,
                  });
                }} />
            ))}
            {(query || activeFilters.length > 0) && (
              <button
                onClick={() => {
                  const name = window.prompt("Name this collection:", query || "My collection");
                  if (!name) return;
                  fetch("/api/collections", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      name,
                      spec: { query: query || undefined, filters: Object.fromEntries(activeFilters) },
                    }),
                  }).then(() => collectionsQuery.refetch());
                }}
                className="mt-1 w-full rounded px-2 py-1 text-left text-[11.5px]"
                style={{ color: "var(--ink-faint)" }}>
                + save this search as a collection
              </button>
            )}
          </Facet>

          {facetsQuery.data && (
            <>
              {Object.keys(facetsQuery.data.categories).length > 0 && (
                <Facet title="Categories">
                  {Object.entries(facetsQuery.data.categories).map(([name, count]) => (
                    <FacetRow key={name} label={CATEGORY_LABELS[name] ?? name} count={count}
                      dotStyle={catStyle(name)}
                      active={params.category === name} onClick={() => setFilter("category", name)} />
                  ))}
                  <button
                    onClick={async () => {
                      const raw = window.prompt(
                        "Describe the category you want. AI designs it, or write 'Name: what belongs in it' to define it yourself.");
                      if (!raw) return;
                      const m = raw.match(/^([^:]{2,40}):\s*(.{10,})$/);
                      const body = m ? { name: m[1].trim(), description: m[2].trim() } : { instruction: raw };
                      const res = await fetch("/api/categories/custom", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                      });
                      const out = await res.json();
                      if (!res.ok) { window.alert(out.detail ?? "failed"); return; }
                      window.alert(`"${out.name}" created · ${out.assigned} items assigned by local embeddings`);
                      facetsQuery.refetch();
                    }}
                    className="mt-1 w-full rounded px-2 py-1 text-left text-[11.5px]"
                    style={{ color: "var(--ink-faint)" }}>
                    + create a category (AI)
                  </button>
                </Facet>
              )}
              <Facet title="Sources">
                {Object.entries(facetsQuery.data.sources).map(([name, count]) => (
                  <FacetRow key={name} label={SOURCE_LABELS[name] ?? name} count={count}
                    active={params.source === name} onClick={() => setFilter("source", name)} />
                ))}
              </Facet>
              <Facet title="Kinds">
                {Object.entries(facetsQuery.data.kinds).map(([name, count]) => (
                  <FacetRow key={name} label={KIND_LABELS[name] ?? name} count={count}
                    active={params.kind === name} onClick={() => setFilter("kind", name)} />
                ))}
              </Facet>
              <Facet title="Domains">
                {Object.entries(facetsQuery.data.domains).slice(0, 22).map(([name, count]) => (
                  <FacetRow key={name} label={name} count={count} mono
                    active={params.domain === name} onClick={() => setFilter("domain", name)} />
                ))}
              </Facet>
            </>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1 border-t hairline px-2 py-2">
          {!readOnly && (
            <SideButton title="Add archives (drag and drop exports)"
              onClick={() => setShowOnboarding(true)} label="Add" />
          )}
          <SideButton title="Today's memory mix" onClick={() => setTodayOpen(true)} label="Today" />
          <SideButton title="Stats" active={page === "stats"}
            onClick={() => setPage(page === "stats" ? "table" : "stats")} label="Stats" />
          {!readOnly && (
            <SideButton title="Lock in: tasks, overdue first"
              onClick={() => setTasksOpen(true)} label="Tasks" />
          )}
          {!readOnly && (
            <SideButton title="Pull fresh data from everywhere (Chrome, notes, feeds)"
              onClick={runSync} label={syncing ? "Sync…" : "Sync"} active={syncing} />
          )}
          <SideButton title="Random item (recall practice)"
            onClick={() => fetchRandom(params.category).then((it) => setDrawerItem(it.id))} label="Random" />
          <SideButton title="Toggle theme" onClick={() => setDark(!dark)} label={dark ? "Day" : "Night"} />
        </div>
      </aside>

      {/* ── reading room ─────────────────────────────── */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2.5 border-b hairline px-4 py-3">
          <input
            ref={searchRef}
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder="Search the archive..."
            className="search-field w-full max-w-2xl rounded-md px-3.5 py-2 text-[13.5px]"
          />
          <div className="segment flex shrink-0 overflow-hidden rounded-md text-[11px]">
            {(["keyword", "semantic", "hybrid"] as const).map((m) => (
              <button key={m}
                onClick={() => setParams({ mode: m })}
                className={`whitespace-nowrap px-3 py-2 capitalize ${mode === m ? "active" : ""}`}>
                {m}
              </button>
            ))}
          </div>
          {!readOnly && (
            <button onClick={() => setAskOpen(true)}
              className="btn-accent shrink-0 rounded-md px-3.5 py-2 text-[12.5px]">
              Ask ✦
            </button>
          )}
          {!readOnly && selected.size > 0 && (
            <button
              onClick={() =>
                import("./api").then(({ exportSelection }) =>
                  exportSelection([...selected], "selection.md").then((r) => {
                    alert(`Exported ${r.count} items to ${r.written}`);
                    setSelected(new Set());
                  })
                )
              }
              className="btn-quiet shrink-0 rounded-md px-3 py-2 text-[12px]">
              Export {selected.size}
            </button>
          )}
        </header>

        {activeFilters.length > 0 && page === "table" && (
          <div className="flex flex-wrap items-center gap-1.5 border-b hairline px-4 py-1.5">
            <span className="smallcaps">filtered:</span>
            {activeFilters.map(([key, value]) => (
              <button key={key} onClick={() => setParams({ [key]: undefined })}
                className="btn-quiet rounded-full px-2.5 py-0.5 text-[11px]">
                {key.replace(/_/g, " ")}: {CATEGORY_LABELS[value!] ?? value} ✕
              </button>
            ))}
          </div>
        )}

        {page === "feed" ? (
          <FeedPage onOpenItem={(id) => setDrawerItem(id)} readOnly={readOnly} />
        ) : page === "companion" ? (
          <CompanionPage seed={companionSeed} onSeedConsumed={() => setCompanionSeed(null)} />
        ) : page === "stats" ? (
          <StatsPage />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto"
            onScroll={(e) => {
              const el = e.currentTarget;
              if (el.scrollHeight - el.scrollTop - el.clientHeight < 500 && itemsQuery.hasNextPage && !itemsQuery.isFetching)
                itemsQuery.fetchNextPage();
            }}>
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10" style={{ background: "var(--bg)" }}>
                <tr className="smallcaps border-b hairline text-left">
                  <th className="w-7 px-2 py-2"></th>
                  <th className="cursor-pointer px-2 py-2 select-none" onClick={() => sortBy("title")}>Entry{sortGlyph("title")}</th>
                  <th className="w-24 cursor-pointer px-2 py-2 select-none" onClick={() => sortBy("category")}>Category{sortGlyph("category")}</th>
                  <th className="w-40 cursor-pointer px-2 py-2 select-none" onClick={() => sortBy("domain")}>Domain{sortGlyph("domain")}</th>
                  <th className="w-14 cursor-pointer px-2 py-2 text-right select-none" onClick={() => sortBy("interest_score")}
                    title="interest score">Score{sortGlyph("interest_score")}</th>
                  <th className="w-24 cursor-pointer px-2 py-2 select-none" onClick={() => sortBy("created_at")}>Date{sortGlyph("created_at")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, i) => (
                  <Row key={item.id} item={item} focused={i === focusRow}
                    selected={selected.has(item.id)}
                    onSelect={() => toggleSelect(item.id)}
                    onOpen={() => { setFocusRow(i); setDrawerItem(item.id); }}
                    onChipClick={(cat) => setFilter("category", cat)} />
                ))}
              </tbody>
            </table>
            {itemsQuery.isFetching && (
              <div className="p-5 text-center text-[12px]" style={{ color: "var(--ink-faint)" }}>
                turning pages...
              </div>
            )}
            {!itemsQuery.isFetching && items.length === 0 && (
              <div className="flex flex-col items-center gap-2 p-16 text-center">
                <div className="font-display text-xl italic" style={{ color: "var(--ink-dim)" }}>
                  The archive is quiet here.
                </div>
                <div className="text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
                  {query ? "Try semantic mode, or Ask ✦ with a fuzzy description." : "Run km ingest, then km extract."}
                </div>
              </div>
            )}
            <div className="flex items-center justify-center gap-3 px-4 py-3 text-[11px]" style={{ color: "var(--ink-faint)" }}>
              <span><kbd>/</kbd> search</span>
              <span><kbd>j</kbd><kbd>k</kbd> move</span>
              <span><kbd>↵</kbd> open</span>
              <span><kbd>s</kbd> star</span>
              <span><kbd>a</kbd> ask</span>
            </div>
          </div>
        )}
      </main>

      {drawerItem !== null && (
        <Drawer itemId={drawerItem} onClose={() => setDrawerItem(null)} readOnly={readOnly}
          onChanged={() => itemsQuery.refetch()} onOpen={(id) => setDrawerItem(id)}
          onChatAbout={(label, url) => {
            setCompanionSeed(`About this entry from my archive: "${label}"${url ? ` (${url})` : ""}\n\n`);
            setDrawerItem(null);
            setPage("companion");
          }} />
      )}
      {askOpen && (
        <AskPanel onClose={() => setAskOpen(false)} onOpenItem={(id) => setDrawerItem(id)}
          filters={{ source: params.source, category: params.category, domain: params.domain }} />
      )}
      {todayOpen && <TodayPanel onClose={() => setTodayOpen(false)} />}
      {tasksOpen && <TasksPanel onClose={() => setTasksOpen(false)} />}
    </div>
  );
}

function Row({ item, focused, selected, onSelect, onOpen, onChipClick }: {
  item: Item; focused: boolean; selected: boolean;
  onSelect: () => void; onOpen: () => void; onChipClick: (c: string) => void;
}) {
  const tweet = isTweetKind(item.kind);
  const label = item.title || item.text || item.url || "(untitled)";
  return (
    <tr className={`row-item cursor-pointer ${focused ? "focused" : ""}`} onClick={onOpen}>
      <td className="px-2 py-2 align-top" onClick={(e) => { e.stopPropagation(); onSelect(); }}>
        <input type="checkbox" checked={selected} readOnly
          className="mt-0.5 accent-[var(--accent)]" />
      </td>
      <td className="max-w-0 truncate px-2 py-2">
        <span className="mr-1.5 inline-block w-3 text-center" style={{ color: "var(--ink-faint)" }}
          title={KIND_LABELS[item.kind] ?? item.kind}>
          {KIND_GLYPHS[item.kind] ?? "·"}
        </span>
        {item.starred && <span className="mr-1" style={{ color: "var(--accent)" }}>★</span>}
        <span className={tweet ? "row-tweet" : "row-title"}>{label}</span>
        {item.author && (
          <span className="font-mono-data ml-1.5" style={{ color: "var(--ink-faint)" }}>@{item.author}</span>
        )}
        {item.is_thread && <span className="ml-1.5 text-[10px]" title="thread">⧉</span>}
        {item.passage && (
          <div className="truncate pl-[18px] text-[12px] italic" style={{ color: "var(--ink-dim)" }}
            title={item.passage}>
            “{item.passage}”
          </div>
        )}
      </td>
      <td className="px-2 py-2">
        {item.category && (
          <button className="chip" style={catStyle(item.category)}
            onClick={(e) => { e.stopPropagation(); onChipClick(item.category!); }}>
            <span className="h-1 w-1 rounded-full" style={{ background: "var(--cat)" }} />
            {CATEGORY_LABELS[item.category] ?? item.category}
          </button>
        )}
      </td>
      <td className="font-mono-data truncate px-2 py-2" style={{ color: "var(--ink-dim)" }}>
        {item.domain}
      </td>
      <td className="font-mono-data px-2 py-2 text-right" style={{ color: "var(--ink-dim)" }}>
        {item.interest_score > 1 ? item.interest_score.toFixed(1) : ""}
      </td>
      <td className="font-mono-data px-2 py-2" style={{ color: "var(--ink-faint)" }}>
        {item.created_at?.slice(0, 10)}
      </td>
    </tr>
  );
}

function Facet({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="smallcaps mb-1.5 px-1">{title}</div>
      <div className="space-y-px">{children}</div>
    </div>
  );
}

function FacetRow({ label, count, active, onClick, dotStyle, mono }: {
  label: string; count?: number; active?: boolean; onClick: () => void;
  dotStyle?: React.CSSProperties; mono?: boolean;
}) {
  return (
    <button onClick={onClick}
      className={`facet-row flex w-full items-center gap-1.5 px-2 py-[3px] text-left text-[12.5px] ${active ? "active" : ""}`}>
      {dotStyle && (
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ ...dotStyle, background: "var(--cat)" }} />
      )}
      <span className={`truncate ${mono ? "font-mono-data" : ""}`}>{label}</span>
      {count !== undefined && (
        <span className="facet-count ml-auto shrink-0">{count.toLocaleString()}</span>
      )}
    </button>
  );
}

function SideButton({ label, title, onClick, active }: {
  label: string; title: string; onClick: () => void; active?: boolean;
}) {
  return (
    <button title={title} onClick={onClick}
      className={`facet-row flex-1 rounded px-2 py-1.5 text-center text-[11px] ${active ? "active" : ""}`}>
      {label}
    </button>
  );
}
