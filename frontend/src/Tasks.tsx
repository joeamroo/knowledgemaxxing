import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

type Task = {
  id: number; text: string; due?: string; status: string; source?: string;
  overdue: boolean; due_today: boolean;
};

export function TasksPanel({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const { data } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => fetch("/api/tasks").then((r) => r.json()) as Promise<{ tasks: Task[] }>,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["tasks"] });

  const add = async () => {
    if (!text.trim()) return;
    await fetch("/api/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.trim(), due: due || null }),
    });
    setText(""); setDue("");
    invalidate();
  };

  const done = (id: number) =>
    fetch(`/api/tasks/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "done" }),
    }).then(invalidate);

  const harvest = () =>
    fetch("/api/tasks/harvest", { method: "POST" }).then(invalidate);

  const tasks = data?.tasks ?? [];
  const overdue = tasks.filter((t) => t.overdue);
  const today = tasks.filter((t) => t.due_today && !t.overdue);
  const rest = tasks.filter((t) => !t.overdue && !t.due_today);

  const Row = ({ t }: { t: Task }) => (
    <li className="flex items-start gap-2.5 py-2 text-[13px]">
      <input type="checkbox" onChange={() => done(t.id)}
        className="mt-0.5 accent-[var(--accent)]" title="Done" />
      <span className="min-w-0 flex-1">
        {t.text}
        {t.due && (
          <span className="font-mono-data ml-2 text-[11px]"
            style={{ color: t.overdue ? "#c96b5a" : "var(--ink-faint)" }}>
            due {t.due}
          </span>
        )}
        {t.source?.startsWith("note:") && (
          <span className="font-mono-data ml-2 text-[10.5px]" style={{ color: "var(--ink-faint)" }}>
            ✎ {t.source.slice(5, 30)}
          </span>
        )}
      </span>
    </li>
  );

  return (
    <div className="backdrop fixed inset-0 z-20 flex justify-end" onClick={onClose}>
      <div className="drawer-panel h-full w-full max-w-md overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-display text-lg italic" style={{ fontWeight: 520 }}>Lock in</h2>
          <button onClick={onClose} className="btn-quiet rounded px-2 py-0.5 text-[12px]">esc</button>
        </div>

        <div className="mb-2 flex gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="What needs doing?"
            className="search-field min-w-0 flex-1 rounded-md px-3 py-2 text-[13px]" />
          <input type="date" value={due} onChange={(e) => setDue(e.target.value)}
            className="search-field w-[130px] rounded-md px-2 py-2 text-[12px]" />
          <button onClick={add} className="btn-accent rounded-md px-3 text-[13px]">Add</button>
        </div>
        <button onClick={harvest} className="mb-5 text-[12px]" style={{ color: "var(--ink-faint)" }}>
          harvest TODOs from your notes →
        </button>

        {overdue.length > 0 && (
          <>
            <div className="smallcaps mb-1" style={{ color: "#c96b5a" }}>
              overdue · {overdue.length}
            </div>
            <ul className="mb-4 divide-y" style={{ borderColor: "var(--hairline)" }}>
              {overdue.map((t) => <Row key={t.id} t={t} />)}
            </ul>
          </>
        )}
        {today.length > 0 && (
          <>
            <div className="smallcaps mb-1" style={{ color: "var(--accent)" }}>today</div>
            <ul className="mb-4">{today.map((t) => <Row key={t.id} t={t} />)}</ul>
          </>
        )}
        <div className="smallcaps mb-1">open · {rest.length}</div>
        <ul>{rest.map((t) => <Row key={t.id} t={t} />)}</ul>
        {tasks.length === 0 && (
          <p className="text-[13px]" style={{ color: "var(--ink-dim)" }}>
            Nothing tracked yet. Add one above, or harvest the TODOs already
            sitting in your notes.
          </p>
        )}
        <p className="mt-6 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
          The secretary and therapist personas see this list, including how long
          things have been overdue.
        </p>
      </div>
    </div>
  );
}
