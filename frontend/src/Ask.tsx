import { useState } from "react";
import { ask, AskResponse } from "./api";
import { KIND_LABELS, SOURCE_LABELS } from "./categories";

export function AskPanel({ onClose, onOpenItem, filters }: {
  onClose: () => void;
  onOpenItem: (id: number) => void;
  filters: Record<string, string | undefined>;
}) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [useAi, setUseAi] = useState(true);

  const run = () => {
    if (!query.trim() || loading) return;
    setLoading(true);
    ask(query, useAi, filters)
      .then(setResult)
      .finally(() => setLoading(false));
  };

  return (
    <div className="backdrop fixed inset-0 z-20 flex items-start justify-center pt-[12vh]" onClick={onClose}>
      <div className="modal-panel w-full max-w-2xl rounded-xl p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="font-display text-lg italic" style={{ fontWeight: 520 }}>
            Ask the archive
          </h2>
          <span className="smallcaps">half-memories welcome</span>
        </div>
        <p className="mb-3 text-[12px]" style={{ color: "var(--ink-dim)" }}>
          Describe what you half-remember; hybrid retrieval finds candidates
          {useAi ? ", then Claude picks the likeliest with a line of reasoning" : ""}.
        </p>

        <div className="mb-1 flex gap-2">
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder='"that tweet where people shared contradictory advice pairs..."'
            className="search-field w-full rounded-md px-3.5 py-2.5 text-[13.5px]"
          />
          <button onClick={run} disabled={loading}
            className="btn-accent shrink-0 rounded-md px-4 text-[13px]">
            {loading ? "consulting..." : "Ask ✦"}
          </button>
        </div>
        <label className="flex items-center gap-1.5 text-[11.5px]" style={{ color: "var(--ink-dim)" }}>
          <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)}
            className="accent-[var(--accent)]" />
          Claude re-rank
        </label>

        {result && (
          <div className="mt-3 max-h-[55vh] overflow-y-auto">
            {result.picks.length > 0 && (
              <>
                <div className="smallcaps mb-1.5">the librarian's picks</div>
                {result.picks.map((pick, i) => (
                  <button key={pick.id} onClick={() => onOpenItem(pick.id)}
                    className="reveal mb-2 block w-full rounded-lg border p-3 text-left transition-colors"
                    style={{
                      borderColor: "var(--accent)",
                      background: i === 0 ? "var(--accent-soft)" : "transparent",
                      animationDelay: `${i * 60}ms`,
                    }}>
                    <div className="text-[13px] font-medium">{pick.title || pick.snippet}</div>
                    <div className="font-display mt-1 text-[12.5px] italic" style={{ color: "var(--accent)" }}>
                      {pick.reasoning}
                    </div>
                  </button>
                ))}
              </>
            )}
            <div className="smallcaps mb-1.5 mt-3">
              {result.picks.length ? "other candidates" : "hybrid results"}
            </div>
            {result.candidates.map((c) => (
              <button key={c.id} onClick={() => onOpenItem(c.id)}
                className="btn-quiet mb-1 block w-full rounded-md p-2.5 text-left text-[12.5px]">
                <span>{c.title || c.snippet}</span>
                <span className="font-mono-data ml-2" style={{ color: "var(--ink-faint)" }}>
                  {KIND_LABELS[c.kind] ?? c.kind} · {(c.sources || []).map((s: string) => SOURCE_LABELS[s] ?? s).join(", ")}
                </span>
              </button>
            ))}
            {result.candidates.length === 0 && (
              <div className="p-4 text-center text-[12.5px]" style={{ color: "var(--ink-faint)" }}>
                Nothing surfaced. Has km embed run yet?
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
