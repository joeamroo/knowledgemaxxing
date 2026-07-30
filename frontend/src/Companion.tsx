import { useEffect, useRef, useState } from "react";

const PERSONA_LABELS: Record<string, string> = {
  therapist: "Therapist",
  companion: "Companion",
  analyst: "Analyst",
  harsh: "Harsh mentor",
  secretary: "Secretary",
  future: "Future",
};

const PERSONA_HINTS: Record<string, string> = {
  therapist: "It has read everything and remembers your past sessions. Start anywhere.",
  companion: "A close friend who has read your whole archive.",
  analyst: "Bold interpretations, held lightly, grounded in your notes.",
  harsh: "Zero flattery. Cites your own notes back at you.",
  secretary: "State of play, what matters today, and a plan. Knows your tasks and feed.",
  future: "Plans your next chapter against your real trajectory.",
};

type Msg = { role: string; content: string };

export function CompanionPage() {
  const [persona, setPersona] = useState("therapist");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setError(null);
    fetch(`/api/talk/history?persona=${persona}`)
      .then((r) => r.json())
      .then((d) => setMessages(d.messages ?? []));
  }, [persona]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const send = async (newSession = false) => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    try {
      const res = await fetch("/api/talk/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona, message: text, new_session: newSession }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? "failed");
      setMessages((m) => [...m, { role: "assistant", content: body.reply }]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b hairline px-5 py-3">
        {Object.entries(PERSONA_LABELS).map(([key, label]) => (
          <button key={key} onClick={() => setPersona(key)}
            className="rounded-full border px-3.5 py-1 text-[12.5px] transition-colors"
            style={{
              borderColor: persona === key ? "var(--accent)" : "var(--hairline)",
              color: persona === key ? "var(--accent)" : "var(--ink-dim)",
            }}>
            {label}
          </button>
        ))}
        <button
          onClick={() => { setMessages([]); setInput(""); }}
          title="Start a fresh session (the last one becomes session notes)"
          className="ml-auto text-[12px]" style={{ color: "var(--ink-faint)" }}>
          new session
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
        {messages.length === 0 && (
          <div className="mx-auto max-w-md pt-16 text-center">
            <div className="brand text-3xl">km<span style={{ color: "var(--accent)" }}>.</span></div>
            <p className="mt-4 text-[14px]" style={{ color: "var(--ink-dim)" }}>
              {PERSONA_HINTS[persona]}
            </p>
            <p className="mt-2 text-[12px]" style={{ color: "var(--ink-faint)" }}>
              Everything stays local except the words of this conversation and the
              text of your archive, sent to Claude with your own key.
            </p>
          </div>
        )}
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.map((m, i) => (
            <div key={i}
              className="max-w-[85%] whitespace-pre-wrap rounded-xl px-4 py-3 text-[14px] leading-relaxed"
              style={m.role === "user"
                ? { alignSelf: "flex-end", background: "var(--bg-raised)", border: "1px solid var(--hairline)" }
                : { alignSelf: "flex-start", background: "var(--bg-inset)", borderLeft: "3px solid var(--accent)" }}>
              {m.content}
            </div>
          ))}
          {busy && (
            <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
              reading the archive...
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
            placeholder={`Talk to the ${PERSONA_LABELS[persona].toLowerCase()}...`}
            rows={2}
            className="search-field flex-1 resize-none rounded-md px-3.5 py-2.5 text-[13.5px]"
          />
          <button onClick={() => send()} disabled={busy || !input.trim()}
            className="btn-accent shrink-0 self-end rounded-md px-5 py-2.5 text-[13px] disabled:opacity-40">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
