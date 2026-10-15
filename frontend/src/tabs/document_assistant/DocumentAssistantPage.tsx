import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteFile,
  extractFile,
  proofreadApply,
  streamEvents,
  type ExtractResult,
  type Source,
  type StreamEvent,
} from "./api";
import InputPanel from "./InputPanel";
import ModeSelector, { type Mode } from "./ModeSelector";
import ProofreadPanel, { IDLE_PROOFREAD, acceptedEdits, type ProofreadState } from "./ProofreadPanel";
import ResultPanel, { type ResultState } from "./ResultPanel";
import RewritePanel, { IDLE_REWRITE, finalText, type RewriteState } from "./RewritePanel";

// Work lives only in this browser tab (spec section 6), mirrored here so a refresh keeps it.
const STORAGE_KEY = "pnb.documentAssistant.v1";

type Style = "formal" | "polite";

type Saved = {
  mode: Mode;
  text: string;
  file: ExtractResult | null;
  result: string;
  style?: Style;
  rewrite?: RewriteState;
  proofread?: ProofreadState;
};

function loadSaved(): Saved | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

/** A stream cut off by a page refresh cannot resume; show what was kept as stopped. */
function restoreRewrite(r: RewriteState | undefined): RewriteState {
  if (!r) return IDLE_REWRITE;
  if (r.status !== "streaming") return r;
  return { ...r, status: "stopped", live: null, text: finalText(r) };
}

function restoreProofread(p: ProofreadState | undefined): ProofreadState {
  if (!p) return IDLE_PROOFREAD;
  return p.status === "checking" ? IDLE_PROOFREAD : p;
}

const IDLE: ResultState = { status: "idle", text: "" };
const INTERRUPTED = { code: "interrupted", message: "The response ended early. Please try again." };

const ACTION_LABEL: Record<Mode, [string, string]> = {
  summarize: ["Summarize", "Summarizing…"],
  rewrite: ["Rewrite", "Rewriting…"],
  proofread: ["Check text", "Checking…"],
  draft: ["Draft", "Drafting…"],
  data: ["Ask", "Thinking…"],
};

export default function DocumentAssistantPage() {
  const [saved] = useState(loadSaved);
  const [mode, setMode] = useState<Mode>(saved?.mode ?? "summarize");
  const [text, setText] = useState(saved?.text ?? "");
  const [file, setFile] = useState<ExtractResult | null>(saved?.file ?? null);
  const [result, setResult] = useState<ResultState>(
    saved?.result ? { status: "done", text: saved.result } : IDLE,
  );
  const [style, setStyle] = useState<Style>(saved?.style ?? "formal");
  const [rewrite, setRewrite] = useState<RewriteState>(() => restoreRewrite(saved?.rewrite));
  const [proofread, setProofread] = useState<ProofreadState>(() =>
    restoreProofread(saved?.proofread),
  );
  const [applying, setApplying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      const data: Saved = { mode, text, file, result: result.text, style, rewrite, proofread };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch {
      // Storage full or blocked: work continues, it just will not survive a refresh.
    }
  }, [mode, text, file, result.text, style, rewrite, proofread]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const streaming =
    result.status === "streaming" || rewrite.status === "streaming" || proofread.status === "checking";

  const resetResults = () => {
    setResult(IDLE);
    setRewrite(IDLE_REWRITE);
    setProofread(IDLE_PROOFREAD);
  };

  const upload = async (f: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const extracted = await extractFile(f);
      if (file) void deleteFile(file.file_id);
      setFile(extracted);
      resetResults();
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.message : "The file could not be uploaded.");
    } finally {
      setUploading(false);
    }
  };

  const clear = () => {
    if (file) void deleteFile(file.file_id);
    setFile(null);
    setText("");
    resetResults();
    setUploadError(null);
  };

  const source = (): Source => (file ? { file_id: file.file_id } : { text });

  const expired = (e: unknown): boolean => {
    if (e instanceof ApiError && e.status === 410) {
      // The server deleted the upload after 4 hours.
      setFile(null);
      setUploadError(e.message);
      resetResults();
      return true;
    }
    return false;
  };

  /**
   * Run one streaming request. `onEvent` handles events; `onEnd` gets "stopped" when the user
   * pressed Stop, "ended" when the stream closed, or an error message.
   */
  const run = async (
    path: "/summarize" | "/rewrite" | "/proofread",
    body: object,
    onEvent: (ev: StreamEvent) => void,
    onEnd: (outcome: "stopped" | "ended" | { message: string }) => void,
  ) => {
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamEvents(path, body, onEvent, controller.signal);
      onEnd(controller.signal.aborted ? "stopped" : "ended");
    } catch (e) {
      if (expired(e)) return;
      onEnd({ message: e instanceof ApiError ? e.message : "Something went wrong. Please try again." });
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const summarize = () => {
    setResult({ status: "streaming", text: "" });
    return run(
      "/summarize",
      source(),
      (ev) =>
        setResult((r) => {
          switch (ev.type) {
            case "status":
              return { ...r, progress: ev.text };
            case "chunk":
              return { ...r, text: r.text + ev.text };
            case "done":
              return { ...r, status: "done" };
            case "error":
              return { ...r, status: "error", error: { code: ev.code, message: ev.message } };
            default:
              return r;
          }
        }),
      (outcome) =>
        setResult((r) => {
          if (outcome === "stopped") return { ...r, status: "stopped" };
          if (outcome !== "ended") return { ...r, status: "error", error: { code: "request", ...outcome } };
          // A stream that ends without "done" or "error" was cut off.
          return r.status === "streaming" ? { ...r, status: "error", error: INTERRUPTED } : r;
        }),
    );
  };

  const startRewrite = () => {
    setRewrite({ ...IDLE_REWRITE, status: "streaming" });
    return run(
      "/rewrite",
      { ...source(), style },
      (ev) =>
        setRewrite((r) => {
          switch (ev.type) {
            case "source":
              return { ...r, source: ev.paragraphs, output: ev.paragraphs.map(() => null) };
            case "status":
              return { ...r, progress: ev.text };
            case "section_start":
              return { ...r, live: { paragraphs: ev.paragraphs, text: "" } };
            case "chunk":
              return r.live ? { ...r, live: { ...r.live, text: r.live.text + ev.text } } : r;
            case "section_end": {
              const output = [...r.output];
              ev.paragraphs.forEach((i, k) => (output[i] = ev.texts[k]));
              return { ...r, output, live: null };
            }
            case "notice":
              return { ...r, notices: [...r.notices, ev.text] };
            case "done": {
              return { ...r, status: "done", live: null, text: finalText(r) };
            }
            case "error":
              return { ...r, status: "error", live: null, error: { code: ev.code, message: ev.message } };
            default:
              return r;
          }
        }),
      (outcome) =>
        setRewrite((r) => {
          if (outcome === "stopped") {
            return r.source.length
              ? { ...r, status: "stopped", live: null, text: finalText(r) }
              : IDLE_REWRITE;
          }
          if (outcome !== "ended") return { ...r, status: "error", error: { code: "request", ...outcome } };
          return r.status === "streaming" ? { ...r, status: "error", live: null, error: INTERRUPTED } : r;
        }),
    );
  };

  const startProofread = () => {
    setProofread({ ...IDLE_PROOFREAD, status: "checking" });
    return run(
      "/proofread",
      source(),
      (ev) =>
        setProofread((p) => {
          switch (ev.type) {
            case "status":
              return { ...p, progress: ev.text };
            case "result":
              return {
                ...p,
                status: "ready",
                text: ev.text,
                suggestions: ev.suggestions,
                notices: ev.notices,
                decisions: {},
              };
            case "error":
              return { ...p, status: "error", error: { code: ev.code, message: ev.message } };
            default:
              return p;
          }
        }),
      (outcome) =>
        setProofread((p) => {
          if (outcome === "stopped") return { ...IDLE_PROOFREAD, status: "stopped" };
          if (outcome !== "ended") return { ...p, status: "error", error: { code: "request", ...outcome } };
          return p.status === "checking" ? { ...p, status: "error", error: INTERRUPTED } : p;
        }),
    );
  };

  const decide = (id: string, replacement: string | null) =>
    setProofread((p) => {
      const decisions = { ...p.decisions };
      // Clicking an accepted fix again undoes it.
      if (replacement === null && typeof decisions[id] === "string") delete decisions[id];
      else decisions[id] = replacement;
      return { ...p, decisions };
    });

  const acceptAll = () =>
    setProofread((p) => {
      const decisions = { ...p.decisions };
      for (const s of p.suggestions) {
        if (s.applicable && !(s.id in decisions) && s.replacements.length > 0) {
          decisions[s.id] = s.replacements[0];
        }
      }
      return { ...p, decisions };
    });

  const apply = async () => {
    setApplying(true);
    try {
      const body: Source = file ? { file_id: file.file_id } : { text: proofread.text };
      const applied = await proofreadApply(body, acceptedEdits(proofread));
      setProofread((p) => ({ ...p, applied }));
    } catch (e) {
      if (expired(e)) return;
      const message = e instanceof ApiError ? e.message : "The changes could not be applied.";
      setProofread((p) => ({ ...p, notices: [...p.notices.filter((n) => n !== message), message] }));
    } finally {
      setApplying(false);
    }
  };

  const stop = () => abortRef.current?.abort();

  const action = mode === "rewrite" ? startRewrite : mode === "proofread" ? startProofread : summarize;
  const dataFileInTextMode = file?.kind === "data" && (mode === "rewrite" || mode === "proofread");
  const canRun =
    !streaming && !uploading && !dataFileInTextMode && (file !== null || text.trim().length > 0);
  const [label, busyLabel] = ACTION_LABEL[mode];

  return (
    <section>
      <h1 className="text-2xl font-semibold text-pnb-maroon">Document Assistant</h1>
      <p className="mt-2 max-w-2xl text-slate-600">
        Proofread, rewrite and summarize letters, circulars, reports and data files. Everything runs
        on this machine.
      </p>

      <div className="mt-8">
        <ModeSelector active={mode} onSelect={setMode} disabled={streaming} />
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-2">
        <div>
          <InputPanel
            text={text}
            onTextChange={setText}
            file={file}
            uploading={uploading}
            uploadError={uploadError}
            onUpload={upload}
            onClear={clear}
            disabled={streaming}
          />
          {mode === "rewrite" && (
            <fieldset className="mt-6" disabled={streaming}>
              <legend className="text-sm font-medium text-slate-700">Style</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {(
                  [
                    ["formal", "Formal / official"],
                    ["polite", "Polite / customer-friendly"],
                  ] as const
                ).map(([id, name]) => (
                  <label
                    key={id}
                    className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm ${
                      style === id
                        ? "border-pnb-maroon bg-pnb-cream text-pnb-maroon"
                        : "border-slate-200 text-slate-600 hover:border-pnb-maroon/40"
                    }`}
                  >
                    <input
                      type="radio"
                      name="style"
                      value={id}
                      checked={style === id}
                      onChange={() => setStyle(id)}
                      className="sr-only"
                    />
                    {name}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {dataFileInTextMode && (
            <p className="mt-4 rounded-md bg-pnb-cream px-3 py-2 text-sm text-slate-700">
              Proofread and Rewrite work on documents and text. For data files, use Summarize.
            </p>
          )}
          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={action}
              disabled={!canRun}
              className="rounded-md bg-pnb-maroon px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-pnb-maroon-dark focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pnb-gold disabled:cursor-not-allowed disabled:opacity-40"
            >
              {streaming ? busyLabel : label}
            </button>
            {!file && text && !streaming && (
              <button
                type="button"
                onClick={clear}
                className="text-sm text-slate-500 hover:text-pnb-maroon"
              >
                Clear
              </button>
            )}
          </div>
        </div>
        {mode === "rewrite" ? (
          <RewritePanel
            rewrite={rewrite}
            file={file}
            onEdit={(t) => setRewrite((r) => ({ ...r, text: t }))}
            onStop={stop}
          />
        ) : mode === "proofread" ? (
          <ProofreadPanel
            proofread={proofread}
            file={file}
            applying={applying}
            onDecide={decide}
            onAcceptAll={acceptAll}
            onApply={apply}
            onReview={() => setProofread((p) => ({ ...p, applied: undefined }))}
            onStop={stop}
          />
        ) : (
          <ResultPanel result={result} onStop={stop} />
        )}
      </div>

      <p className="mt-10 border-t border-slate-100 pt-4 text-xs text-slate-500">
        Your work is kept only in this browser tab and is lost when you close it. Uploaded files
        are deleted from the server after 4 hours. English (UK) text only.
      </p>
    </section>
  );
}
