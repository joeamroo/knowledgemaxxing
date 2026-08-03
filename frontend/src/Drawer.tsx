import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchItem, patchItem } from "./api";
import { CATEGORIES, CATEGORY_LABELS, KIND_LABELS, SOURCE_LABELS, catStyle } from "./categories";

const isTweetKind = (k: string) =>
  ["like", "retweet", "own_tweet", "bookmark_tweet"].includes(k);

export function Drawer({ itemId, onClose, onChanged, onOpen, onChatAbout, readOnly }: {
  itemId: number; onClose: () => void; onChanged: () => void;
  onOpen?: (id: number) => void;
  onChatAbout?: (label: string, url?: string | null) => void;
  readOnly?: boolean;
}) {
  const [reader, setReader] = useState<string | null>(null);
  const [readerBusy, setReaderBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const note2 = (msg: string) => {
    setFlash(msg);
    setTimeout(() => setFlash(null), 1600);
  };
  const { data: item, refetch } = useQuery({
    queryKey: ["item", itemId],
    queryFn: () => fetchItem(itemId),
  });
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<
    { url: string; title: string; why?: string; similarity?: number }[] | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  const discover = async () => {
    setDiscovering(true);
    setDiscoverError(null);
    try {
      const res = await fetch(`/api/items/${itemId}/discover`, { method: "POST" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? "discovery failed");
      setDiscovered(body.picks ?? []);
    } catch (e: unknown) {
      setDiscoverError(e instanceof Error ? e.message : "discovery failed");
    } finally {
      setDiscovering(false);
    }
  };

  const { data: similar } = useQuery({
    queryKey: ["similar", itemId],
    queryFn: () =>
      fetch(`/api/items/${itemId}/similar`).then((r) => r.json()) as Promise<{
        items: { id: number; kind: string; title?: string; text?: string; domain?: string; reasons?: string[] }[];
      }>,
    staleTime: 60_000,
  });
  const [note, setNote] = useState<string | null>(null);

  if (!item) return null;
  const mutate = (patch: Parameters<typeof patchItem>[1]) =>
    patchItem(itemId, patch).then(() => { refetch(); onChanged(); });

  return (
    <div className="backdrop fixed inset-0 z-20 flex justify-end" onClick={onClose}>
      <div className="drawer-panel h-full w-full max-w-lg overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}>

        <div className="mb-1 flex items-start justify-between gap-3">
          <div className="smallcaps pt-1">{KIND_LABELS[item.kind] ?? item.kind}
            {item.is_essay && " · essay"}{item.is_thread && " · thread"}
            {item.in_reading_list && " · reading list"}
          </div>
          <button onClick={onClose} className="btn-quiet rounded px-2 py-0.5 text-[12px]">esc</button>
        </div>

        {item.title && (
          <h2 className="font-display mb-3 text-[21px] leading-snug" style={{ fontWeight: 560 }}>
            {item.title}
          </h2>
        )}
        {item.author && (
          <div className="font-mono-data mb-3" style={{ color: "var(--ink-dim)" }}>@{item.author}</div>
        )}

        {item.text && (
          <blockquote className={`quote-block mb-4 whitespace-pre-wrap rounded-r-md px-4 py-3 ${isTweetKind(item.kind) ? "" : "not-italic"}`}>
            {item.text}
          </blockquote>
        )}

        {item.url && (
          <a href={item.url} target="_blank" rel="noreferrer"
            className="font-mono-data mb-4 block truncate hover:underline"
            style={{ color: "var(--accent)" }}>
            {item.url} ↗
          </a>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          {item.url && (
            <button onClick={() => window.open(item.url!, "_blank")}
              className="btn-accent rounded-md px-3 py-1.5 text-[12px]">
              Open ↗
            </button>
          )}
          {item.url && (
            <button onClick={() => navigator.clipboard.writeText(item.url!).then(() => note2("link copied"))}
              className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">Copy link</button>
          )}
          <button onClick={() => {
            const label = item.title || (item.text || "").slice(0, 120);
            navigator.clipboard.writeText(
              `> ${(item.text || item.title || "").slice(0, 500)}\n\n[${label}](${item.url ?? ""})`
            ).then(() => note2("markdown copied"));
          }} className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">Copy MD</button>
          {item.url && !readOnly && (
            <button disabled={readerBusy} onClick={() => {
              if (reader) { setReader(null); return; }
              setReaderBusy(true);
              fetch(`/api/items/${itemId}/readable`, { method: "POST" })
                .then((r) => r.json())
                .then((b) => setReader(b.text ?? b.detail ?? "nothing readable"))
                .finally(() => setReaderBusy(false));
            }} className="btn-quiet rounded-md px-3 py-1.5 text-[12px] disabled:opacity-50">
              {readerBusy ? "fetching..." : reader ? "Close reader" : "Read here"}
            </button>
          )}
          {!readOnly && (
            <button onClick={() =>
              fetch(`/api/feed/queue/${itemId}`, { method: "POST" }).then(() => note2("in today's feed"))
            } className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">Read later</button>
          )}
          {!readOnly && (
            <button onClick={() => {
              const label = item.title || (item.text || "").slice(0, 100) || item.url || "this";
              fetch("/api/tasks", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: `Read: ${label}`.slice(0, 180) }),
              }).then(() => note2("task added"));
            }} className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">Make task</button>
          )}
          {!readOnly && (
            <button onClick={() =>
              onChatAbout?.(item.title || (item.text || "").slice(0, 200), item.url)
            } className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">Chat about this ✦</button>
          )}
          {flash && (
            <span className="text-[11.5px]" style={{ color: "var(--accent)" }}>{flash}</span>
          )}
        </div>

        {reader && (
          <div className="quote-block mb-4 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-r-md px-4 py-3 text-[13px] not-italic"
            style={{ color: "var(--ink-dim)" }}>
            {reader}
          </div>
        )}

        <div className="mb-5 flex flex-wrap items-center gap-2">
          {!readOnly && (<>
          <button onClick={() => mutate({ starred: !item.starred })}
            className={`btn-quiet rounded-md px-3 py-1.5 text-[12px] ${item.starred ? "!border-[var(--accent)]" : ""}`}
            style={item.starred ? { color: "var(--accent)" } : undefined}>
            {item.starred ? "★ Starred" : "☆ Star"}
          </button>
          <button onClick={() => mutate({ archived: !item.archived })}
            className="btn-quiet rounded-md px-3 py-1.5 text-[12px]">
            {item.archived ? "Unarchive" : "Archive"}
          </button>
          <select
            value={item.category ?? ""}
            onChange={(e) => mutate({ category_override: e.target.value })}
            className="btn-quiet rounded-md bg-transparent px-2 py-1.5 text-[12px]"
            style={{ background: "var(--bg-raised)" }}>
            <option value="" disabled>recategorize...</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>
            ))}
          </select>
          </>)}
          {item.category && (
            <span className="chip" style={catStyle(item.category)}>
              <span className="h-1 w-1 rounded-full" style={{ background: "var(--cat)" }} />
              {CATEGORY_LABELS[item.category] ?? item.category}
            </span>
          )}
        </div>

        <div className="smallcaps mb-2">Provenance, every occurrence</div>
        <ul className="mb-5 space-y-1.5">
          {item.occurrences?.map((o, i) => (
            <li key={i} className="flex items-baseline gap-2 text-[12.5px]">
              <span className="h-1 w-1 shrink-0 translate-y-[-2px] rounded-full" style={{ background: "var(--accent)" }} />
              <span>
                {KIND_LABELS[o.kind] ?? o.kind}
                <span style={{ color: "var(--ink-dim)" }}> via {SOURCE_LABELS[o.source_kind] ?? o.source_kind}</span>
                {o.detail && (
                  <span className="font-mono-data" style={{ color: "var(--ink-faint)" }}> · {o.detail.slice(0, 60)}</span>
                )}
              </span>
              <span className="font-mono-data ml-auto shrink-0" style={{ color: "var(--ink-faint)" }}>
                {o.occurred_at?.slice(0, 10)}
              </span>
            </li>
          ))}
        </ul>

        {(similar?.items?.length ?? 0) > 0 && (
          <>
            <div className="smallcaps mb-2">More like this</div>
            <ul className="mb-5 space-y-1">
              {similar!.items.map((s) => (
                <li key={s.id}>
                  <button
                    onClick={() => onOpen?.(s.id)}
                    className="block w-full truncate rounded px-2 py-1.5 text-left text-[12.5px] hover:underline"
                    style={{ background: "var(--bg-raised)" }}>
                    <span style={{ color: "var(--ink-faint)" }}>
                      {KIND_LABELS[s.kind] ?? s.kind} ·{" "}
                    </span>
                    {(s.title || s.text || "").slice(0, 110)}
                    {s.reasons && s.reasons.length > 0 && (
                      <span className="font-mono-data ml-1.5 text-[10px]" style={{ color: "var(--accent)" }}>
                        {s.reasons.join(" · ")}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {item.url && !readOnly && (
          <div className="mb-5">
            <button onClick={discover} disabled={discovering}
              className="btn-quiet rounded-md px-3 py-1.5 text-[12px] disabled:opacity-50">
              {discovering ? "walking the web..." : "Find similar on the web ✦"}
            </button>
            {discoverError && (
              <div className="mt-2 text-[12px]" style={{ color: "#c96b5a" }}>{discoverError}</div>
            )}
            {discovered && (
              <ul className="mt-2 space-y-1.5">
                {discovered.length === 0 && (
                  <li className="text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
                    nothing close enough found this time
                  </li>
                )}
                {discovered.map((d, i) => (
                  <li key={i} className="rounded px-2 py-1.5 text-[12.5px]"
                    style={{ background: "var(--bg-raised)" }}>
                    <a href={d.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {d.title.slice(0, 100)}
                    </a>
                    {d.similarity != null && (
                      <span className="font-mono-data ml-2" style={{ color: "var(--ink-faint)" }}>
                        {d.similarity.toFixed(2)}
                      </span>
                    )}
                    {d.why && (
                      <div style={{ color: "var(--ink-faint)" }}>{d.why}</div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="smallcaps mb-1.5">Marginalia</div>
        {readOnly ? (
          <div className="quote-block rounded-r-md px-4 py-3 text-[13px]"
            style={{ color: "var(--ink-dim)" }}>
            {item.note || "no notes on this one"}
          </div>
        ) : (
          <textarea
            value={note ?? item.note ?? ""}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => note !== null && mutate({ note })}
            placeholder="A note in the margin..."
            className="search-field h-24 w-full rounded-md p-3 text-[13px]"
          />
        )}
        <div className="font-mono-data mt-3" style={{ color: "var(--ink-faint)" }}>
          interest {item.interest_score?.toFixed(1)} · first seen {item.created_at?.slice(0, 10) ?? "unknown"}
        </div>
      </div>
    </div>
  );
}
