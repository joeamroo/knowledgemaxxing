import { useEffect, useRef, useState } from "react";

const PERSONA_LABELS: Record<string, string> = {
  archivist: "Archivist",
  therapist: "Therapist",
  companion: "Companion",
  analyst: "Analyst",
  harsh: "Harsh mentor",
  secretary: "Secretary",
  future: "Future",
};

const PERSONA_HINTS: Record<string, string> = {
  archivist: "Searches your whole history live: finds half-remembered passages, builds lists of links, digs into anything you ever read or saved.",
  therapist: "It has read everything and remembers your past sessions. Start anywhere.",
  companion: "A close friend who has read your whole archive.",
  analyst: "Bold interpretations, held lightly, grounded in your notes.",
  harsh: "Zero flattery. Cites your own notes back at you.",
  secretary: "State of play, what matters today, and a plan. Knows your tasks and feed.",
  future: "Plans your next chapter against your real trajectory.",
};

type Msg = { role: string; content: string; tools?: string[] };

type Tab = {
  id: string;              // stable client-side id
  persona: string;
  session: string | null;  // null until the first reply names the session file
  label: string;
  messages: Msg[];
  busy: boolean;
  loaded: boolean;         // history fetched for an existing session
};

type SessionInfo = { session: string; persona: string; label: string; messages: number };

/* chat bubbles carry markdown lists and links; render the minimum well:
   [label](url) and bare urls become anchors, **bold** becomes bold */
function renderChat(text: string) {
  const parts = text.split(/(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s)]+|\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    const md = p.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (md) return <a key={i} href={md[2]} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--accent)" }}>{md[1]}</a>;
    if (/^https?:\/\//.test(p)) return <a key={i} href={p} target="_blank" rel="noreferrer" className="underline break-all" style={{ color: "var(--accent)" }}>{p}</a>;
    const bold = p.match(/^\*\*([^*]+)\*\*$/);
    if (bold) return <strong key={i}>{bold[1]}</strong>;
    return p;
  });
}

const newTab = (persona = "archivist"): Tab => ({
  id: Math.random().toString(36).slice(2),
  persona, session: null, label: "new chat", messages: [], busy: false, loaded: true,
});

function loadStoredTabs(): { tabs: Tab[]; active: number } {
  try {
    const raw = JSON.parse(localStorage.getItem("km-chat-tabs") ?? "");
    if (Array.isArray(raw.tabs) && raw.tabs.length) {
      return {
        tabs: raw.tabs.map((t: Partial<Tab>) => ({
          ...newTab(t.persona ?? "archivist"),
          session: t.session ?? null,
          label: t.label ?? "chat",
          loaded: !t.session, // sessions need a history fetch
        })),
        active: Math.min(raw.active ?? 0, raw.tabs.length - 1),
      };
    }
  } catch { /* first run */ }
  return { tabs: [newTab()], active: 0 };
}

export function CompanionPage({ seed, onSeedConsumed }: {
  seed?: string | null; onSeedConsumed?: () => void;
}) {
  const stored = useRef(loadStoredTabs());
  const [tabs, setTabs] = useState<Tab[]>(stored.current.tabs);
  const [active, setActive] = useState(stored.current.active);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [spend, setSpend] = useState<{ month_usd: number; budget_usd: number } | null>(null);
  const [recent, setRecent] = useState<SessionInfo[] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const tab = tabs[active];

  const patchTab = (id: string, patch: Partial<Tab> | ((t: Tab) => Partial<Tab>)) =>
    setTabs((ts) => ts.map((t) => t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t));

  useEffect(() => {
    fetch("/api/spend").then((r) => r.json()).then(setSpend).catch(() => {});
  }, []);

  useEffect(() => {
    if (seed) {
      setInput(seed);
      onSeedConsumed?.();
    }
  }, [seed]);

  // persist tab shells (not message bodies) across reloads
  useEffect(() => {
    localStorage.setItem("km-chat-tabs", JSON.stringify({
      tabs: tabs.map((t) => ({ persona: t.persona, session: t.session, label: t.label })),
      active,
    }));
  }, [tabs, active]);

  // fetch history when switching to a tab with an unloaded session
  useEffect(() => {
    if (!tab || tab.loaded || !tab.session) return;
    const id = tab.id;
    fetch(`/api/talk/history?session=${encodeURIComponent(tab.session)}`)
      .then((r) => r.ok ? r.json() : Promise.reject())
      .then((d) => patchTab(id, { messages: d.messages ?? [], loaded: true }))
      .catch(() => patchTab(id, { session: null, loaded: true, label: "new chat" }));
  }, [active, tabs.length]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [tab?.messages.length, tab?.busy, active]);

  const send = async () => {
    const text = input.trim();
    if (!text || !tab || tab.busy) return;
    const id = tab.id;
    setError(null);
    setInput("");
    patchTab(id, (t) => ({
      busy: true,
      messages: [...t.messages, { role: "user", content: text }],
      label: t.session ? t.label : text.slice(0, 40),
    }));
    try {
      const res = await fetch("/api/talk/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          persona: tab.persona,
          message: text,
          session: tab.session ?? "",   // "" = fresh session for this tab
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? "failed");
      patchTab(id, (t) => ({
        busy: false,
        session: body.session,
        messages: [...t.messages, { role: "assistant", content: body.reply, tools: body.tools_used }],
      }));
      if (body.spend) setSpend(body.spend);
    } catch (e: unknown) {
      patchTab(id, { busy: false });
      setError(e instanceof Error ? e.message : "something went wrong");
    }
  };

  const closeTab = (id: string) => {
    setTabs((ts) => {
      const next = ts.filter((t) => t.id !== id);
      return next.length ? next : [newTab()];
    });
    setActive((a) => Math.max(0, Math.min(a, tabs.length - 2)));
  };

  const openRecent = (s: SessionInfo) => {
    const existing = tabs.findIndex((t) => t.session === s.session);
    if (existing >= 0) { setActive(existing); setRecent(null); return; }
    const t: Tab = { ...newTab(s.persona), session: s.session, label: s.label.slice(0, 40), loaded: false };
    setTabs((ts) => [...ts, t]);
    setActive(tabs.length);
    setRecent(null);
  };

  if (!tab) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* ── tab bar: parallel conversations ── */}
      <div className="flex items-center gap-1 overflow-x-auto border-b hairline px-3 pt-2">
        {tabs.map((t, i) => (
          <div key={t.id}
            className="flex max-w-[180px] shrink-0 cursor-pointer items-center gap-1.5 rounded-t-md border border-b-0 px-3 py-1.5 text-[12px]"
            style={{
              borderColor: "var(--hairline)",
              background: i === active ? "var(--bg-inset)" : "transparent",
              color: i === active ? "var(--ink)" : "var(--ink-dim)",
            }}
            onClick={() => setActive(i)}>
            {t.busy && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full" style={{ background: "var(--accent)" }} />}
            <span className="truncate">{PERSONA_LABELS[t.persona]?.[0] ?? "?"} · {t.label}</span>
            <button onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}
              className="shrink-0 px-0.5" style={{ color: "var(--ink-faint)" }}>×</button>
          </div>
        ))}
        <button onClick={() => { setTabs((ts) => [...ts, newTab(tab.persona)]); setActive(tabs.length); }}
          title="New chat tab" className="shrink-0 px-2 py-1 text-[15px]" style={{ color: "var(--ink-faint)" }}>
          +
        </button>
        <div className="relative shrink-0">
          <button
            onClick={() => recent ? setRecent(null) :
              fetch("/api/talk/sessions").then((r) => r.json()).then((d) => setRecent(d.sessions ?? []))}
            className="px-2 py-1 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
            history ▾
          </button>
          {recent && (
            <div className="absolute left-0 top-8 z-30 max-h-72 w-72 overflow-y-auto rounded-md border p-1"
              style={{ borderColor: "var(--hairline)", background: "var(--bg-raised)" }}>
              {recent.length === 0 && (
                <div className="px-2 py-1.5 text-[12px]" style={{ color: "var(--ink-faint)" }}>no past sessions</div>
              )}
              {recent.map((s) => (
                <button key={s.session} onClick={() => openRecent(s)}
                  className="block w-full truncate rounded px-2 py-1.5 text-left text-[12px] hover:underline">
                  <span style={{ color: "var(--ink-faint)" }}>{PERSONA_LABELS[s.persona] ?? s.persona} · </span>
                  {s.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── persona picker (locked once a tab has a session) + spend ── */}
      <div className="flex flex-wrap items-center gap-2 border-b hairline px-5 py-2.5">
        {Object.entries(PERSONA_LABELS).map(([key, label]) => (
          <button key={key}
            disabled={!!tab.session && tab.persona !== key}
            onClick={() => !tab.session && patchTab(tab.id, { persona: key })}
            className="rounded-full border px-3.5 py-1 text-[12.5px] transition-colors disabled:opacity-30"
            style={{
              borderColor: tab.persona === key ? "var(--accent)" : "var(--hairline)",
              color: tab.persona === key ? "var(--accent)" : "var(--ink-dim)",
            }}>
            {label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-3">
          {spend && spend.budget_usd > 0 && (
            <span className="font-mono-data text-[11px]"
              title="Estimated AI spend this month vs your budget (ai_monthly_budget_usd in config.yaml)"
              style={{ color: spend.month_usd >= spend.budget_usd * 0.8 ? "#c96b5a" : "var(--ink-faint)" }}>
              ${spend.month_usd.toFixed(2)} / ${spend.budget_usd.toFixed(0)}
            </span>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
        {tab.messages.length === 0 && (
          <div className="mx-auto max-w-md pt-16 text-center">
            <div className="brand text-3xl">km<span style={{ color: "var(--accent)" }}>.</span></div>
            <p className="mt-4 text-[14px]" style={{ color: "var(--ink-dim)" }}>
              {PERSONA_HINTS[tab.persona]}
            </p>
            <p className="mt-2 text-[12px]" style={{ color: "var(--ink-faint)" }}>
              Everything stays local except the words of this conversation and the
              text of your archive, sent to Claude with your own key.
            </p>
          </div>
        )}
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {tab.messages.map((m, i) => (
            <div key={i} className="flex max-w-[85%] flex-col gap-1"
              style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start" }}>
              <div
                className="whitespace-pre-wrap rounded-xl px-4 py-3 text-[14px] leading-relaxed"
                style={m.role === "user"
                  ? { background: "var(--bg-raised)", border: "1px solid var(--hairline)" }
                  : { background: "var(--bg-inset)", borderLeft: "3px solid var(--accent)" }}>
                {m.role === "user" ? m.content : renderChat(m.content)}
              </div>
              {m.tools && m.tools.length > 0 && (
                <div className="font-mono-data px-1 text-[10.5px]" style={{ color: "var(--ink-faint)" }}
                  title={m.tools.join("\n")}>
                  searched: {m.tools.slice(0, 3).join(" · ")}{m.tools.length > 3 ? ` +${m.tools.length - 3}` : ""}
                </div>
              )}
            </div>
          ))}
          {tab.busy && (
            <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
              {tab.persona === "archivist" ? "searching the archive..." : "reading the archive..."}
            </div>
          )}
          {error && (
            <div className="rounded-md border px-4 py-3 text-[13px]"
              style={{ borderColor: "var(--hairline)", color: "#c96b5a" }}>
              {error}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="border-t hairline p-4">
        <div className="mx-auto flex max-w-2xl gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            placeholder={`Talk to the ${PERSONA_LABELS[tab.persona].toLowerCase()}...`}
            rows={2}
            className="search-field flex-1 resize-none rounded-md px-3.5 py-2.5 text-[13.5px]"
          />
          <button onClick={send} disabled={tab.busy || !input.trim()}
            className="btn-accent shrink-0 self-end rounded-md px-5 py-2.5 text-[13px] disabled:opacity-40">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
