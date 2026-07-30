import { useCallback, useRef, useState } from "react";

type UploadResult = {
  file: string;
  recognized_as?: string;
  items?: number;
  already_known?: number;
  error?: string;
};

const SOURCE_LABELS: Record<string, string> = {
  twitter_archive_zip: "Twitter/X archive",
  takeout_zip: "Google Takeout",
  chat_export: "AI chat export",
  reddit_gdpr: "Reddit export",
  chrome_export: "browser history export",
  bookmarks_html: "bookmarks file",
  chrome_bookmarks: "Chrome bookmarks",
  my_activity: "Google My Activity",
};

export function Onboarding({ onDone, onSkip, skipLabel = "skip, I'll use the CLI" }: {
  onDone: () => void; onSkip: () => void; skipLabel?: string;
}) {
  const [results, setResults] = useState<UploadResult[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const ingestedTotal = results.reduce((a, r) => a + (r.items ?? 0), 0);

  const uploadFiles = useCallback(async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      setBusy(file.name);
      try {
        const res = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, {
          method: "POST",
          body: file,
        });
        const body = await res.json();
        setResults((r) => [
          ...r,
          res.ok
            ? { file: file.name, ...body }
            : { file: file.name, error: body.detail ?? "upload failed" },
        ]);
      } catch {
        setResults((r) => [...r, { file: file.name, error: "upload failed" }]);
      }
    }
    setBusy(null);
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center p-6"
      style={{ background: "var(--bg)", color: "var(--ink)" }}>
      <div className="reveal w-full max-w-xl">
        <div className="mb-8 text-center">
          <div className="brand text-4xl">
            km<span style={{ color: "var(--accent)" }}>.</span>
          </div>
          <div className="smallcaps mt-2">knowledgemaxxing</div>
          <p className="mx-auto mt-5 max-w-md text-[14.5px]" style={{ color: "var(--ink-dim)" }}>
            Turn your digital exhaust into a private, searchable knowledge base.
            Everything stays in one SQLite file on this machine. This page is served
            from 127.0.0.1 and makes no external calls.
          </p>
        </div>

        <div className="mb-4 rounded-md border hairline p-5" style={{ background: "var(--bg-raised)" }}>
          <div className="smallcaps mb-2">step 1 · gather your archives</div>
          <p className="text-[13.5px]" style={{ color: "var(--ink-dim)" }}>
            Request your exports: a Twitter/X archive zip, Google Takeout (Chrome history
            and My Activity), ChatGPT or Claude data exports, a Reddit GDPR export, or
            browser bookmark files. Already have them in Downloads? Drop them below.
          </p>
        </div>

        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
          }}
          onClick={() => fileRef.current?.click()}
          className="mb-4 cursor-pointer rounded-md border-2 border-dashed p-10 text-center transition-colors"
          style={{
            borderColor: dragOver ? "var(--accent)" : "var(--hairline)",
            background: dragOver ? "var(--bg-raised)" : "transparent",
          }}>
          <div className="smallcaps mb-2">step 2 · drop archives here</div>
          <p className="text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
            zip, json, csv, or html exports · or click to choose files
          </p>
          <input ref={fileRef} type="file" multiple className="hidden"
            onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        </div>

        {(results.length > 0 || busy) && (
          <div className="mb-4 space-y-1.5 rounded-md border hairline p-4"
            style={{ background: "var(--bg-raised)" }}>
            {results.map((r, i) => (
              <div key={i} className="flex items-baseline gap-2 text-[13px]">
                <span className="font-mono-data truncate" style={{ maxWidth: "45%" }}>{r.file}</span>
                {r.error ? (
                  <span style={{ color: "var(--danger, #c96b5a)" }}>{r.error}</span>
                ) : (
                  <span style={{ color: "var(--ink-dim)" }}>
                    {SOURCE_LABELS[r.recognized_as ?? ""] ?? r.recognized_as} ·{" "}
                    {r.items ? `${r.items.toLocaleString()} items ingested` : "already ingested"}
                  </span>
                )}
              </div>
            ))}
            {busy && (
              <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
                ingesting {busy}...
              </div>
            )}
          </div>
        )}

        <div className="mb-6 rounded-md border hairline p-5" style={{ background: "var(--bg-raised)" }}>
          <div className="smallcaps mb-2">step 3 · go deeper from the terminal</div>
          <p className="text-[13.5px]" style={{ color: "var(--ink-dim)" }}>
            <span className="font-mono-data">km discover</span> finds archives across your
            disk (it will ask before ingesting anything), <span className="font-mono-data">km login</span>{" "}
            + <span className="font-mono-data">km fetch</span> scrape your X bookmarks and Reddit
            saves with your own session, and <span className="font-mono-data">km fetch apple-notes</span>{" "}
            needs macOS automation permission the first time it runs.
          </p>
        </div>

        <div className="flex items-center justify-center gap-5">
          <button
            onClick={onDone}
            disabled={ingestedTotal === 0}
            className="rounded-md px-6 py-2.5 text-[14px] font-semibold disabled:opacity-40"
            style={{ background: "var(--accent)", color: "#1c1508" }}>
            Enter your archive{ingestedTotal > 0 ? ` (${ingestedTotal.toLocaleString()} items)` : ""}
          </button>
          <button onClick={onSkip} className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
            {skipLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
