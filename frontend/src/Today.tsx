import { useQuery } from "@tanstack/react-query";
import { KIND_LABELS } from "./categories";

type Digest = {
  date: string;
  on_this_day: { years_ago: number; kind: string; label: string; url: string | null }[];
  gems: { label: string; title: string; snippet: string; url: string | null }[];
};

export function TodayPanel({ onClose }: { onClose: () => void }) {
  const { data } = useQuery<Digest>({
    queryKey: ["digest"],
    queryFn: () => fetch("/api/digest").then((r) => r.json()),
  });

  return (
    <div className="backdrop fixed inset-0 z-20 flex items-start justify-center pt-[10vh]" onClick={onClose}>
      <div className="modal-panel w-full max-w-xl rounded-xl p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-display text-xl italic" style={{ fontWeight: 540 }}>
            Today in the archive
          </h2>
          <span className="font-mono-data" style={{ color: "var(--ink-faint)" }}>{data?.date}</span>
        </div>

        {!data && <div style={{ color: "var(--ink-faint)" }}>opening the drawers...</div>}

        {data && data.on_this_day.length > 0 && (
          <>
            <div className="smallcaps mb-2">on this day</div>
            <ul className="mb-5 space-y-2">
              {data.on_this_day.map((entry, i) => (
                <li key={i} className="reveal text-[13px]" style={{ animationDelay: `${i * 60}ms` }}>
                  <span className="font-mono-data mr-2" style={{ color: "var(--accent)" }}>
                    {entry.years_ago}y ago
                  </span>
                  <span style={{ color: "var(--ink-faint)" }}>{KIND_LABELS[entry.kind] ?? entry.kind} · </span>
                  {entry.url ? (
                    <a href={entry.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {entry.label}
                    </a>
                  ) : entry.label}
                </li>
              ))}
            </ul>
          </>
        )}

        {data?.gems.map((gem, i) => (
          <div key={i} className="reveal mb-4" style={{ animationDelay: `${200 + i * 80}ms` }}>
            <div className="smallcaps mb-1">{gem.label}</div>
            <div className="font-display text-[14.5px] italic leading-relaxed">
              {gem.url ? (
                <a href={gem.url} target="_blank" rel="noreferrer" className="hover:underline">
                  {gem.title}
                </a>
              ) : gem.title}
            </div>
            {gem.snippet && gem.snippet !== gem.title && (
              <div className="mt-1 text-[12px]" style={{ color: "var(--ink-dim)" }}>
                {gem.snippet}
              </div>
            )}
          </div>
        ))}

        {data && data.on_this_day.length === 0 && data.gems.length === 0 && (
          <div style={{ color: "var(--ink-faint)" }}>
            Nothing surfaced today; come back tomorrow.
          </div>
        )}
      </div>
    </div>
  );
}
