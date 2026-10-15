const BASE = "/api/document-assistant";

export type SheetPreview = {
  name: string;
  columns: string[];
  row_count: number;
  rows: (string | number | boolean | null)[][];
};

export type ExtractResult = {
  file_id: string;
  filename: string;
  kind: "text" | "data";
  notices: string[];
  expires_at: number;
  text?: string;
  word_count?: number;
  row_count?: number;
  sheets?: SheetPreview[];
};

export type Suggestion = {
  id: string;
  start: number;
  end: number;
  category: string;
  message: string;
  replacements: string[];
  source: "languagetool" | "llm";
  applicable: boolean;
  // Safe for "Accept all": one replacement, or the LLM made the same fix independently.
  auto_accept?: boolean;
  // The first replacement is the LLM's pick, using the sentence context.
  recommended?: boolean;
};

export type SourceParagraph = {
  text: string;
  editable: boolean;
  reason: string;
  // The original text between this paragraph and the previous one ("\n\n" or "\n").
  before?: string;
};

export type StreamEvent =
  | { type: "status"; text: string }
  | { type: "chunk"; text: string; index?: number }
  | { type: "done" }
  | { type: "error"; code: string; message: string }
  | { type: "notice"; text: string }
  // Rewrite
  | { type: "source"; paragraphs: SourceParagraph[] }
  | { type: "section_start"; index: number; paragraphs: number[] }
  | { type: "section_end"; index: number; paragraphs: number[]; texts: string[] }
  // Proofread
  | { type: "result"; text: string; suggestions: Suggestion[]; notices: string[] };

export type Source = { text: string } | { file_id: string };

/** A file ready to download, with warnings about formatting it may have lost. */
export type FileResult = { file_id: string; filename: string; warnings: string[] };

export type AppliedResult = {
  text: string;
  warnings: string[];
  file_id?: string;
  filename?: string;
};

/** An error with a plain-English message safe to show to users. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const SERVER_DOWN = "Could not reach the server. Please check that the backend is running.";

async function errorFrom(resp: Response): Promise<ApiError> {
  let message = "Something went wrong. Please try again.";
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // Not JSON: keep the generic message rather than showing raw server output.
  }
  return new ApiError(message, resp.status);
}

export async function extractFile(file: File): Promise<ExtractResult> {
  const form = new FormData();
  form.append("file", file);
  let resp: Response;
  try {
    resp = await fetch(`${BASE}/extract`, { method: "POST", body: form });
  } catch {
    throw new ApiError(SERVER_DOWN, 0);
  }
  if (!resp.ok) throw await errorFrom(resp);
  return resp.json();
}

export async function deleteFile(fileId: string): Promise<void> {
  try {
    await fetch(`${BASE}/files/${encodeURIComponent(fileId)}`, { method: "DELETE" });
  } catch {
    // Files expire on their own after 4 hours; a failed delete is not worth an error.
  }
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(SERVER_DOWN, 0);
  }
  if (!resp.ok) throw await errorFrom(resp);
  return resp.json();
}

export function proofreadApply(
  source: Source,
  accepted: { start: number; end: number; replacement: string }[],
): Promise<AppliedResult> {
  return postJson("/proofread/apply", { ...source, accepted });
}

export function exportResult(body: {
  text: string;
  format: "txt" | "docx";
  source_file_id?: string;
}): Promise<FileResult> {
  return postJson("/export", body);
}

export function downloadUrl(fileId: string): string {
  return `${BASE}/download/${encodeURIComponent(fileId)}`;
}

export function streamSummary(
  body: Source,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return streamEvents("/summarize", body, onEvent, signal);
}

/**
 * POST a request and call `onEvent` for each server-sent event.
 * EventSource cannot send a POST body, so the stream is read with fetch.
 * Aborting `signal` stops the stream and, on the server, the model.
 */
export async function streamEvents(
  path: "/summarize" | "/rewrite" | "/proofread",
  body: object,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    if (signal.aborted) return;
    throw new ApiError(SERVER_DOWN, 0);
  }
  if (!resp.ok || !resp.body) throw await errorFrom(resp);

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        for (const line of frame.split("\n")) {
          if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as StreamEvent);
        }
      }
    }
  } catch {
    if (signal.aborted) return;
    throw new ApiError("The connection was interrupted. Please try again.", 0);
  }
}
