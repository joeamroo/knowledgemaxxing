import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

type FeedItem = {
  item_id: number; reason: string; read: number;
  title?: string; text?: string; url?: string; domain?: string; created_at?: string;
};

const REASON_STYLE: Record<string, string> = {
  "new today": "var(--accent)",
  "saved, never read": "#7ba7bc",
  "you read this once, years ago": "#9c8ac2",
  "buried in your saves": "#7fae8f",
};

export function FeedPage({ onOpenItem }: { onOpenItem: (id: number) => void }) {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const { data } = useQuery({
    queryKey: ["feed"],
    queryFn: () => fetch("/api/feed").then((r) => r.json()) as Promise<{ items: FeedItem[] }>,
  });

  const markRead = (id: number) =>
    fetch(`/api/feed/read/${id}`, { method: "POST" }).then(() =>
      qc.invalidateQueries({ queryKey: ["feed"] }));

  const refresh = async () => {
    setRefreshing(true);
    try {
      await fetch("/api/feed/refresh", { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["feed"] });
    } finally {
      setRefreshing(false);
    }
  };

  const items = data?.items ?? [];
  const unread = items.filter((i) => !i.read).length;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-7">
      <div className="reveal mx-auto max-w-2xl">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="font-display text-[26px]" style={{ fontWeight: 560 }}>
              Today's reading
            </h1>
            <div className="smallcaps mt-1">
              {unread} unread · fresh posts and buried gems from your own trail
            </div>
          </div>
          <button onClick={refresh} disabled={refreshing}
            className="btn-quiet rounded-md px-3.5 py-2 text-[12.5px] disabled:opacity-50">
            {refreshing ? "fetching feeds..." : "Refresh feeds"}
          </button>
        </div>

        {items.length === 0 && (
          <p className="text-[14px]" style={{ color: "var(--ink-dim)" }}>
            Nothing yet. Hit Refresh feeds: km probes RSS on the blogs you actually
            visit and mixes fresh posts with things you saved and never read.
          </p>
        )}

        <div className="space-y-3">
          {items.map((it) => {
            const label = it.title || (it.text || "").slice(0, 120) || it.url || "";
            return (
              <div key={it.item_id}
                className="flex items-start gap-3 rounded-lg border hairline p-4"
                style={{ background: "var(--bg-raised)", opacity: it.read ? 0.45 : 1 }}>
                <input type="checkbox" checked={!!it.read}
                  onChange={() => markRead(it.item_id)}
                  title="Mark read"
                  className="mt-1 accent-[var(--accent)]" />
                <div className="min-w-0 flex-1">
                  {it.url ? (
                    <a href={it.url} target="_blank" rel="noreferrer"
                      className="block text-[15px] font-medium leading-snug hover:underline">
                      {label}
                    </a>
                  ) : (
                    <button onClick={() => onOpenItem(it.item_id)}
                      className="block text-left text-[15px] font-medium leading-snug hover:underline">
                      {label}
                    </button>
                  )}
                  <div className="font-mono-data mt-1.5 flex items-center gap-2 text-[11px]"
                    style={{ color: "var(--ink-faint)" }}>
                    <span className="h-1.5 w-1.5 rounded-full"
                      style={{ background: REASON_STYLE[it.reason] ?? "var(--accent)" }} />
                    <span>{it.reason}</span>
                    {it.domain && <span>· {it.domain}</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
