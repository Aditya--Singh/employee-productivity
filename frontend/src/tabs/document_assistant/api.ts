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

export type StreamEvent =
  | { type: "status"; text: string }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; code: string; message: string };

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

/**
 * POST a summary request and call `onEvent` for each server-sent event.
 * EventSource cannot send a POST body, so the stream is read with fetch.
 * Aborting `signal` stops the stream and, on the server, the model.
 */
export async function streamSummary(
  body: { text: string } | { file_id: string },
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}/summarize`, {
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
