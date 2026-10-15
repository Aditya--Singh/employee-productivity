import { useEffect, useRef, useState } from "react";
import { ApiError, deleteFile, extractFile, streamSummary, type ExtractResult } from "./api";
import InputPanel from "./InputPanel";
import ModeSelector, { type Mode } from "./ModeSelector";
import ResultPanel, { type ResultState } from "./ResultPanel";

// Work lives only in this browser tab (spec section 6), mirrored here so a refresh keeps it.
const STORAGE_KEY = "pnb.documentAssistant.v1";

type Saved = { mode: Mode; text: string; file: ExtractResult | null; result: string };

function loadSaved(): Saved | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

const IDLE: ResultState = { status: "idle", text: "" };

export default function DocumentAssistantPage() {
  const [saved] = useState(loadSaved);
  const [mode, setMode] = useState<Mode>(saved?.mode ?? "summarize");
  const [text, setText] = useState(saved?.text ?? "");
  const [file, setFile] = useState<ExtractResult | null>(saved?.file ?? null);
  const [result, setResult] = useState<ResultState>(
    saved?.result ? { status: "done", text: saved.result } : IDLE,
  );
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      const data: Saved = { mode, text, file, result: result.text };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch {
      // Storage full or blocked: work continues, it just will not survive a refresh.
    }
  }, [mode, text, file, result.text]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const streaming = result.status === "streaming";

  const upload = async (f: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const extracted = await extractFile(f);
      if (file) void deleteFile(file.file_id);
      setFile(extracted);
      setResult(IDLE);
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
    setResult(IDLE);
    setUploadError(null);
  };

  const summarize = async () => {
    const controller = new AbortController();
    abortRef.current = controller;
    setResult({ status: "streaming", text: "" });
    const body = file ? { file_id: file.file_id } : { text };
    try {
      await streamSummary(
        body,
        (ev) => {
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
            }
          });
        },
        controller.signal,
      );
      if (controller.signal.aborted) {
        setResult((r) => ({ ...r, status: "stopped" }));
      } else {
        // A stream that ends without "done" or "error" was cut off.
        setResult((r) =>
          r.status === "streaming"
            ? {
                ...r,
                status: "error",
                error: { code: "interrupted", message: "The response ended early. Please try again." },
              }
            : r,
        );
      }
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
      if (e instanceof ApiError && e.status === 410) {
        // The server deleted the upload after 4 hours.
        setFile(null);
        setUploadError(message);
        setResult(IDLE);
        return;
      }
      setResult((r) => ({ ...r, status: "error", error: { code: "request", message } }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  const canRun = !streaming && !uploading && (file !== null || text.trim().length > 0);

  return (
    <section>
      <h1 className="text-2xl font-semibold text-pnb-maroon">Document Assistant</h1>
      <p className="mt-2 max-w-2xl text-slate-600">
        Summarize letters, circulars, reports and data files. Everything runs on this machine.
      </p>

      <div className="mt-8">
        <ModeSelector active={mode} onSelect={setMode} />
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
          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={summarize}
              disabled={!canRun}
              className="rounded-md bg-pnb-maroon px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-pnb-maroon-dark focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-pnb-gold disabled:cursor-not-allowed disabled:opacity-40"
            >
              {streaming ? "Summarizing…" : "Summarize"}
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
        <ResultPanel result={result} onStop={stop} />
      </div>

      <p className="mt-10 border-t border-slate-100 pt-4 text-xs text-slate-500">
        Your work is kept only in this browser tab and is lost when you close it. Uploaded files
        are deleted from the server after 4 hours. English (UK) text only.
      </p>
    </section>
  );
}
