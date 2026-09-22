export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "/api";

const TOKEN_KEY = "teacherai.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string) {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private mode -- session stays in memory only */
  }
}

export function clearToken() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? `Request failed (${res.status})`);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type Me = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  permissions: string[];
};

export type Credits = {
  monthly_allocation_cents: number;
  used_cents: number;
  remaining_cents: number;
  period_start: string;
};

export type Conversation = {
  id: string;
  title: string;
  model: string;
  updated_at: string;
};

export type ChatMessage = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  /** Set when this turn produced a picture; rendered inline in the thread. */
  image_url?: string | null;
  /** The image model used to generate the image, if any. */
  image_model?: string | null;
  /** Set when this turn produced a downloadable file (e.g. PPTX). */
  file_url?: string | null;
  file_name?: string | null;
  /** Set when the backend asks the user to pick an aspect ratio. */
  ratio_options?: string[];
  ratio_labels?: string[];
  /** Files the *user* attached to this turn. Populated at send time and on
   *  history reload by parsing the `<!-- attachments: [...] -->` marker in the
   *  stored content. Rendered as thumbnails / chips beside the user text. */
  attachments?: Attachment[];
  pending?: boolean;
};

/**
 * Pull the `<!-- attachments: ["id1","id2"] -->` marker from a stored user
 * message and return both the visible text and the parsed IDs.
 */
export function parseAttachmentMarker(content: string): { text: string; ids: string[] } {
  const marker = /\n?<!-- attachments: (\[.*?\]) -->/s;
  const match = content.match(marker);
  if (!match) return { text: content, ids: [] };
  let ids: string[] = [];
  try {
    const parsed = JSON.parse(match[1]);
    if (Array.isArray(parsed)) ids = parsed.filter((x): x is string => typeof x === "string");
  } catch { /* ignore */ }
  return { text: content.replace(marker, "").trimEnd(), ids };
}

/** Batch-fetch attachment metadata by id, tolerating individual 404s. */
export async function fetchAttachments(ids: readonly string[]): Promise<Attachment[]> {
  const unique = Array.from(new Set(ids));
  const results = await Promise.all(
    unique.map(async (id) => {
      try {
        return await api<Attachment>(`/uploads/${id}`);
      } catch {
        return null;
      }
    }),
  );
  return results.filter((a): a is Attachment => a !== null);
}

export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

// GPT Image 2.5 family only. Both tiers cost the same; Flare is optimised for
// interactive speed, Sunburst for maximum fidelity. gpt-image-1 is retired
// (results were markedly worse and cost ~3x at high for the same prompt).
//
// The previous list also carried `qwen-image-3.0-pro`, which is not on this
// API key -- the backend silently swapped it for Flare with no warning, so
// users picked Qwen and got Flare. Removed to stop the lie.
// All image models the picker knows about. The actual set shown to the user
// is filtered against the server's `/api/config/image-providers` reply, so a
// model advertised here that the backend can't reach is silently hidden.
export const IMAGE_MODELS = [
  "gpt-image-2.5-flare",
  "gpt-image-2.5-sunburst",
  "qwen-image",
] as const;

/** Rich metadata for the premium model picker in the composer. */
export type ImageModelInfo = {
  id: string;
  label: string;
  tagline: string;
  strengths: string[];
  speedScore: number; // 1..5, 5 = fastest
  qualityScore: number; // 1..5, 5 = highest fidelity
  /** When set, the picker shows this string instead of a per-image cost -- for
   *  subscription-priced providers like Nexum where per-call billing is n/a. */
  billing?: string;
  /** The `provider` this model needs to be reachable. Defaults to "openai". */
  provider?: "openai" | "nexum_qwen";
};

export const IMAGE_MODEL_INFO: Record<string, ImageModelInfo> = {
  "gpt-image-2.5-flare": {
    id: "gpt-image-2.5-flare",
    label: "Flare",
    tagline: "Fast, versatile",
    strengths: ["Interactive edits", "Everyday posters", "Low latency"],
    speedScore: 5,
    qualityScore: 4,
  },
  "gpt-image-2.5-sunburst": {
    id: "gpt-image-2.5-sunburst",
    label: "Sunburst",
    tagline: "Maximum fidelity",
    strengths: ["Detail-rich scenes", "Text-heavy layouts", "Print output"],
    speedScore: 3,
    qualityScore: 5,
  },
  "qwen-image": {
    id: "qwen-image",
    label: "Qwen Image",
    tagline: "Stylized · CJK text · 2K native",
    strengths: ["Illustration", "Non-English text", "Bold posters"],
    speedScore: 4,
    qualityScore: 4,
    billing: "Included in Nexum plan",
    provider: "nexum_qwen",
  },
};

/** Which image providers are enabled on the server. Fetched at page load. */
export type ImageProvidersConfig = {
  openai: boolean;
  nexum_qwen: boolean;
};

let _providersCache: ImageProvidersConfig | null = null;

export async function getImageProviders(): Promise<ImageProvidersConfig> {
  if (_providersCache) return _providersCache;
  try {
    _providersCache = await api<ImageProvidersConfig>("/config/image-providers");
  } catch {
    _providersCache = { openai: true, nexum_qwen: false };
  }
  return _providersCache;
}

/** Filter the full model catalogue to just the ones the server can actually reach. */
export function availableImageModels(providers: ImageProvidersConfig): string[] {
  return IMAGE_MODELS.filter((m) => {
    const info = IMAGE_MODEL_INFO[m];
    const need = info?.provider ?? "openai";
    return providers[need];
  });
}

/** Kept for backward compatibility with the old dropdown -- do not add new callers. */
export const IMAGE_MODEL_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(IMAGE_MODEL_INFO).map(([k, v]) => [k, v.label]),
);
export const IMAGE_QUALITIES = ["low", "medium", "high"] as const;

export type PptTheme = {
  id: string;
  label: string;
  primary: string;
  accent: string;
  bg: string;
};

export const PPT_THEMES: PptTheme[] = [
  { id: "emerald",  label: "Emerald Classroom", primary: "#059669", accent: "#f59e0b", bg: "#ffffff" },
  { id: "ocean",    label: "Ocean Blue",        primary: "#0369a1", accent: "#38bdf8", bg: "#f0f9ff" },
  { id: "sunset",   label: "Warm Sunset",       primary: "#c2410c", accent: "#fb923c", bg: "#fff7ed" },
  { id: "lavender", label: "Lavender",          primary: "#7c3aed", accent: "#c084fc", bg: "#faf5ff" },
  { id: "minimal",  label: "Minimal Dark",      primary: "#18181b", accent: "#a1a1aa", bg: "#ffffff" },
];
export const IMAGE_SIZES = [
  { value: "1024x1024", label: "1:1 Square" },
  { value: "1536x1024", label: "3:2 Landscape" },
  { value: "1024x1536", label: "2:3 Portrait" },
] as const;

export type ImageStyle = {
  id: string;
  label: string;
  tagline: string;
  swatch_from: string;
  swatch_to: string;
};

let _stylesCache: ImageStyle[] | null = null;

export async function getImageStyles(): Promise<ImageStyle[]> {
  if (_stylesCache) return _stylesCache;
  try {
    const res = await api<{ styles: ImageStyle[] }>("/config/image-styles");
    _stylesCache = res.styles;
  } catch {
    _stylesCache = [];
  }
  return _stylesCache;
}

/**
 * Rough cost per image; hint only, not a bill.
 *
 * These are token-metered models, so the real cost per call varies with the
 * expanded prompt length and the output resolution. Numbers below are the
 * measured cost of a typical school-poster prompt on 2026-09-20.
 * Landscape/portrait costs ~1.5x square at the same quality tier.
 */
const IMAGE_COST: Record<string, Record<string, Record<string, number>>> = {
  "gpt-image-2.5-flare": {
    low:    { "1024x1024": 0.6, "1536x1024": 0.9, "1024x1536": 0.9 },
    medium: { "1024x1024": 1.3, "1536x1024": 2.0, "1024x1536": 2.0 },
    high:   { "1024x1024": 5.3, "1536x1024": 8.0, "1024x1536": 8.0 },
  },
  "gpt-image-2.5-sunburst": {
    low:    { "1024x1024": 0.6, "1536x1024": 0.9, "1024x1536": 0.9 },
    medium: { "1024x1024": 1.3, "1536x1024": 2.0, "1024x1536": 2.0 },
    high:   { "1024x1024": 5.3, "1536x1024": 8.0, "1024x1536": 8.0 },
  },
};

export function imageCostHint(model: string, quality: string, size: string = "1024x1024"): string {
  const cents = IMAGE_COST[model]?.[quality]?.[size];
  return cents === undefined ? "" : `~${cents.toFixed(1)}¢ per image`;
}

/**
 * POST a JSON body and save the binary response as a download.
 *
 * Kept separate from `api()` because that helper parses JSON; these endpoints
 * return a DOCX/PDF/PPTX body plus the filename in Content-Disposition.
 */
export async function downloadFile(
  path: string,
  body: Record<string, unknown>,
  fallbackName: string,
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? `Download failed (${res.status})`);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? fallbackName;

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export type Attachment = {
  id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  chars_extracted: number;
  preview?: string | null;
};

export const ACCEPTED_UPLOADS =
  ".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.gif";

/**
 * Upload one file. Deliberately does not reuse `api()`: the browser must set
 * its own multipart Content-Type with the boundary, so we cannot send JSON headers.
 */
export async function uploadFile(file: File): Promise<Attachment> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/uploads`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail ?? `Upload failed (${res.status})`);
  }
  return res.json() as Promise<Attachment>;
}
