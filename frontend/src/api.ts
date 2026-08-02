export type Item = {
  id: number;
  kind: string;
  url: string | null;
  domain: string | null;
  title: string | null;
  text: string | null;
  author: string | null;
  created_at: string | null;
  is_essay: boolean;
  is_thread: boolean;
  in_reading_list: boolean;
  interest_score: number;
  category: string | null;
  starred: boolean;
  archived: boolean;
  note: string | null;
  passage?: string;
  sources: string[];
  occurrences?: { kind: string; occurred_at: string | null; detail: string | null; source_kind: string }[];
};

export type ItemsResponse = { items: Item[]; next_cursor: number | null };

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

export function fetchItems(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return fetch(`/api/items?${search}`).then((r) => json<ItemsResponse>(r));
}

export const fetchItem = (id: number) => fetch(`/api/items/${id}`).then((r) => json<Item>(r));

export function patchItem(id: number, patch: Partial<Pick<Item, "starred" | "archived" | "note"> & { category_override: string }>) {
  return fetch(`/api/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then((r) => json<Item>(r));
}

export const fetchFacets = (params: Record<string, string | undefined>) => {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) search.set(k, v);
  return fetch(`/api/facets?${search}`).then((r) =>
    json<{ kinds: Record<string, number>; domains: Record<string, number>; categories: Record<string, number>; sources: Record<string, number> }>(r)
  );
};

export const fetchStats = () => fetch(`/api/stats`).then((r) => json<any>(r));

export const fetchMeta = () => fetch(`/api/meta`).then((r) => json<{ read_only: boolean }>(r));

export const fetchRandom = (category?: string) =>
  fetch(`/api/random${category ? `?category=${category}` : ""}`).then((r) => json<Item>(r));

export type AskResponse = { mode: string; picks: (Item & { reasoning: string; snippet: string })[]; candidates: any[] };

export const ask = (query: string, ai: boolean, filters: Record<string, string | undefined>) =>
  fetch(`/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, ai, k: 20, ...filters }),
  }).then((r) => json<AskResponse>(r));

export const exportSelection = (ids: number[], filename: string) =>
  fetch(`/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, filename }),
  }).then((r) => json<{ written: string; count: number }>(r));
