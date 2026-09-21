import { API_BASE, ApiError, clearToken, getToken } from "./api";

type ToolStreamHandlers = {
  onDelta: (text: string) => void;
  onError?: (message: string) => void;
  onDone?: (data: { prompt_tokens: number; completion_tokens: number; cost_cents: number }) => void;
};

/**
 * POST /api/tools/<tool> and consume the SSE body.
 */
export async function streamTool(
  tool: string,
  body: Record<string, unknown>,
  handlers: ToolStreamHandlers,
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/tools/${tool}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? `Tool request failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      let raw = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) raw += line.slice(6);
      }
      if (!raw) continue;
      let payload: any;
      try { payload = JSON.parse(raw); } catch { continue; }
      if (event === "delta") handlers.onDelta(payload.content ?? "");
      else if (event === "done") handlers.onDone?.(payload);
      else if (event === "error") handlers.onError?.(payload.message ?? "Unknown error");
    }
  }
}

type StreamHandlers = {
  onStart?: (data: {
    conversation_id: string;
    model: string;
    kind?: string;
    mode_label?: string;
    reasoning_effort?: "low" | "medium" | "high";
  }) => void;
  onDelta: (text: string) => void;
  onStatus?: (message: string) => void;
  onImage?: (data: { url: string; caption: string; edited: boolean }) => void;
  onFile?: (data: { url: string; filename: string; caption: string }) => void;
  onDone?: (data: {
    conversation_id: string;
    prompt_tokens: number;
    completion_tokens: number;
    cost_cents: number;
  }) => void;
  onError?: (message: string) => void;
};

/**
 * POST /api/chat and consume the SSE body.
 *
 * EventSource cannot issue a POST or carry an Authorization header, so we read
 * the response body ourselves and parse the `event:`/`data:` frames.
 */
export async function streamChat(
  body: {
    message: string;
    conversation_id?: string | null;
    model?: string | null;
    mode?: "auto" | "luna" | "terra" | "sol";
    reasoning_effort?: "low" | "medium" | "high";
    size?: string;
    quality?: string;
    image_model?: string;
    attachment_ids?: string[];
    use_knowledge_base?: boolean;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (res.status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? `Chat failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line; keep any partial tail in the buffer.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      let raw = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) raw += line.slice(6);
      }
      if (!raw) continue;

      let payload: any;
      try {
        payload = JSON.parse(raw);
      } catch {
        continue;
      }

      if (event === "start") handlers.onStart?.(payload);
      else if (event === "status") handlers.onStatus?.(payload.message ?? "");
      else if (event === "image") handlers.onImage?.(payload);
      else if (event === "file") handlers.onFile?.(payload);
      else if (event === "delta") handlers.onDelta(payload.content ?? "");
      else if (event === "done") handlers.onDone?.(payload);
      else if (event === "error") handlers.onError?.(payload.message ?? "Unknown error");
    }
  }
}
