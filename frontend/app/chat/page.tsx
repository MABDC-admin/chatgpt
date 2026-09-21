"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import AuthedImage from "@/components/AuthedImage";
import Markdown from "@/components/Markdown";
import {
  ACCEPTED_UPLOADS,
  API_BASE,
  api,
  availableImageModels,
  getImageProviders,
  getToken,
  uploadFile,
  type Attachment,
  IMAGE_MODELS,
  IMAGE_QUALITIES,
  IMAGE_SIZES,
  type ChatMessage,
  type Conversation,
  type Credits,
  type Me,
} from "@/lib/api";
import { streamChat } from "@/lib/stream";
import { cn } from "@/lib/utils";
import ImageModelPicker from "@/components/ImageModelPicker";
import TextModelPicker, { type ReasoningEffort, type TextMode } from "@/components/TextModelPicker";
import { AlertCircle, Paperclip, ArrowUp, Square, X, FileText, Image as ImageIcon, Table as TableIcon, Sparkles, Plus } from "lucide-react";

const ATTACHMENT_ICONS: Record<string, typeof FileText> = {
  pdf: FileText,
  docx: FileText,
  pptx: TableIcon,
  xlsx: TableIcon,
  csv: TableIcon,
  image: ImageIcon,
};

export default function ChatPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [credits, setCredits] = useState<Credits | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [textMode, setTextMode] = useState<TextMode>("auto");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("medium");
  // Default to Flare -- fast and same price as Sunburst. Was IMAGE_MODELS[2]
  // (Qwen), which is no longer in the list and would crash the index access.
  const [imageModel, setImageModel] = useState<string>(IMAGE_MODELS[0]);
  const [imageQuality, setImageQuality] = useState<string>("medium");
  const [imageSize, setImageSize] = useState<string>("1024x1024");
  // Set from /api/config/image-providers on mount so we hide models the
  // server can't reach (e.g. Qwen when NEXUM_API_KEY is unset).
  const [availableModels, setAvailableModels] = useState<string[]>(IMAGE_MODELS.slice());
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [useKnowledge, setUseKnowledge] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const [showCreditRequest, setShowCreditRequest] = useState(false);
  const [requestingCredits, setRequestingCredits] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const searchRef = useRef("");
  const activeIdRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + "px";
    }
  }, [draft]);

  const refreshCredits = useCallback(async () => {
    try {
      setCredits(await api<Credits>("/auth/me/credits"));
    } catch {
      /* non-fatal */
    }
  }, []);

  const refreshConversations = useCallback(async (term?: string) => {
    const query = (term ?? searchRef.current).trim();
    try {
      setConversations(
        await api<Conversation[]>(
          query ? `/conversations?q=${encodeURIComponent(query)}` : "/conversations",
        ),
      );
    } catch {
      /* non-fatal */
    }
  }, []);

  const searchConversations = useCallback(
    (term: string) => {
      searchRef.current = term;
      void refreshConversations(term);
    },
    [refreshConversations],
  );

  const renameConversation = useCallback(
    async (id: string, title: string) => {
      try {
        await api(`/conversations/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
        await refreshConversations();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not rename that chat");
      }
    },
    [refreshConversations],
  );

  const archiveConversation = useCallback(
    async (id: string) => {
      try {
        await api(`/conversations/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ archived: true }),
        });
        if (id === activeIdRef.current) {
          setActiveId(null);
          setMessages([]);
        }
        await refreshConversations();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not archive that chat");
      }
    },
    [refreshConversations],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        await api(`/conversations/${id}`, { method: "DELETE" });
        if (id === activeIdRef.current) {
          setActiveId(null);
          setMessages([]);
        }
        await refreshConversations();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not delete that chat");
      }
    },
    [refreshConversations],
  );

  useEffect(() => {
    try {
      const m = window.localStorage.getItem("teacherai.imageModel");
      const q = window.localStorage.getItem("teacherai.imageQuality");
      const s = window.localStorage.getItem("teacherai.imageSize");
      const textModePreference = window.localStorage.getItem("teacherai.textMode");
      const reasoningPreference = window.localStorage.getItem("teacherai.reasoningEffort");
      if (m && (IMAGE_MODELS as readonly string[]).includes(m)) setImageModel(m);
      if (q && (IMAGE_QUALITIES as readonly string[]).includes(q)) setImageQuality(q);
      if (s && IMAGE_SIZES.some((sz) => sz.value === s)) setImageSize(s);
      if (textModePreference && ["auto", "luna", "terra", "sol"].includes(textModePreference)) {
        setTextMode(textModePreference as TextMode);
      }
      if (reasoningPreference && ["low", "medium", "high"].includes(reasoningPreference)) {
        setReasoningEffort(reasoningPreference as ReasoningEffort);
      }
    } catch {
      /* private mode */
    }

    // Resolve which providers this server has configured -- controls whether
    // the Qwen tile shows up in the picker at all.
    (async () => {
      const providers = await getImageProviders();
      const models = availableImageModels(providers);
      if (models.length > 0) {
        setAvailableModels(models);
        // If the persisted preference points at a model this server can't
        // serve, drop back to the first supported one.
        setImageModel((cur) => (models.includes(cur) ? cur : models[0]));
      }
    })();
  }, []);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        setMe(await api<Me>("/auth/me"));
        await Promise.all([refreshCredits(), refreshConversations()]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load your account");
      }
    })();
  }, [router, refreshCredits, refreshConversations]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function openConversation(id: string | null) {
    abortRef.current?.abort();
    setActiveId(id);
    setError(null);
    if (!id) {
      setMessages([]);
      return;
    }
    try {
      const detail = await api<{ messages: ChatMessage[] }>(`/conversations/${id}`);
      setMessages(detail.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open that conversation");
    }
  }

  async function onPickFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    for (const file of Array.from(files)) {
      try {
        const attached = await uploadFile(file);
        setAttachments((prev) => [...prev, attached]);
      } catch (err) {
        setError(err instanceof Error ? err.message : `Could not upload ${file.name}`);
      }
    }
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  // Paste images from clipboard (Ctrl+V)
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      if (!e.clipboardData?.items) return;
      // Don't intercept if user is typing in textarea
      if (document.activeElement === textareaRef.current && e.clipboardData.items.length > 0) {
        const hasImage = Array.from(e.clipboardData.items).some(
          (item) => item.type.startsWith("image/")
        );
        if (!hasImage) return; // let normal text paste happen
      }
      const files: File[] = [];
      for (const item of Array.from(e.clipboardData.items)) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) files.push(file);
        }
      }
      if (files.length > 0) {
        e.preventDefault();
        const dt = new DataTransfer();
        files.forEach((f) => dt.items.add(f));
        void onPickFiles(dt.files);
      }
    };
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, [onPickFiles]);

  // Drag-and-drop handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files?.length) {
      void onPickFiles(e.dataTransfer.files);
    }
  }, [onPickFiles]);

  async function send() {
    const text = draft.trim();
    if (!text || streaming) return;

    setDraft("");
    setError(null);
    const sentWith = attachments;
    setAttachments([]);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        {
          message: text,
          conversation_id: activeId,
          mode: textMode,
          reasoning_effort: reasoningEffort,
          image_model: imageModel,
          size: imageSize,
          quality: imageQuality,
          attachment_ids: sentWith.map((a) => a.id),
          use_knowledge_base: useKnowledge,
        },
        {
          onStart: ({ conversation_id }) => setActiveId(conversation_id),
          onStatus: (msg) =>
            setMessages((prev) => {
              const next = [...prev];
              next[next.length - 1] = { role: "assistant", content: msg, pending: true };
              return next;
            }),
          onImage: ({ url, caption }) =>
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              // If the last message is an empty/pending placeholder, replace it
              if (last?.role === "assistant" && !last.image_url && (!last.content || last.pending)) {
                next[next.length - 1] = {
                  role: "assistant",
                  content: caption,
                  image_url: url,
                };
              } else {
                // Bulk slides: append each new image as its own message
                next.push({
                  role: "assistant",
                  content: caption,
                  image_url: url,
                });
              }
              return next;
            }),
          onDelta: (piece) =>
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = {
                role: "assistant",
                content: (last.pending ? "" : last.content) + piece,
              };
              return next;
            }),
          onFile: ({ url, filename, caption }) =>
            setMessages((prev) => {
              const next = [...prev];
              next.push({
                role: "assistant",
                content: caption,
                file_url: url,
                file_name: filename,
              });
              return next;
            }),
          onError: (message) => {
            setError(message);
            const lower = message.toLowerCase();
            if (lower.includes("credit") || lower.includes("quota") || lower.includes("insufficient") || lower.includes("not enough")) {
              setShowCreditRequest(true);
            } else {
              setShowCreditRequest(false);
            }
          },
          onDone: () => {
            void refreshCredits();
            void refreshConversations();
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : "The assistant could not respond");
        setAttachments(sentWith);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  async function notifyAdminForCredits() {
    setRequestingCredits(true);
    try {
      await api("/auth/me/request-credits", { method: "POST" });
      setError(null);
      setShowCreditRequest(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to send notification.";
      setError(msg);
    } finally {
      setRequestingCredits(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
      <Sidebar
        me={me}
        credits={credits}
        conversations={conversations}
        activeId={activeId}
        onSelect={(id) => void openConversation(id)}
        onSearch={searchConversations}
        onRename={(id, title) => void renameConversation(id, title)}
        onArchive={(id) => void archiveConversation(id)}
        onDelete={(id) => void deleteConversation(id)}
      />

      <main className="flex flex-1 flex-col min-w-0 relative">
        {/* Top bar controls */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-emerald-100 bg-white flex-wrap md:pl-6">
          <TextModelPicker
            mode={textMode}
            reasoningEffort={reasoningEffort}
            onChange={({ mode, reasoningEffort: nextReasoningEffort }) => {
              setTextMode(mode);
              setReasoningEffort(nextReasoningEffort);
              try {
                window.localStorage.setItem("teacherai.textMode", mode);
                window.localStorage.setItem("teacherai.reasoningEffort", nextReasoningEffort);
              } catch {
                /* private mode */
              }
            }}
          />
          <ImageModelPicker
            availableModels={availableModels}
            model={imageModel}
            quality={imageQuality}
            size={imageSize}
            onChange={({ model, quality, size }) => {
              setImageModel(model);
              setImageQuality(quality);
              setImageSize(size);
              try {
                window.localStorage.setItem("teacherai.imageModel", model);
                window.localStorage.setItem("teacherai.imageQuality", quality);
                window.localStorage.setItem("teacherai.imageSize", size);
              } catch {
                /* private mode */
              }
            }}
          />

          <label className="inline-flex items-center gap-2 text-sm text-[#6b7280] cursor-pointer select-none ml-auto">
            <input
              type="checkbox"
              checked={useKnowledge}
              onChange={(e) => setUseKnowledge(e.target.checked)}
              className="h-4 w-4 rounded border-emerald-300 text-emerald-600 accent-emerald-600 cursor-pointer"
            />
            <BookOpenIcon className="h-3.5 w-3.5" />
            Knowledge base
          </label>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto py-6">
          {messages.length === 0 && (
            <div className="mx-auto max-w-3xl px-6 flex flex-col items-center justify-center h-full text-center space-y-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 border border-emerald-100">
                <Sparkles className="h-7 w-7 text-emerald-600" />
              </div>
              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-[#1f2937]">How can I help you today?</h2>
                <p className="text-sm text-[#6b7280] max-w-md">
                  Ask me to plan a lesson, draft an assessment, or explain a topic.
                  Or describe an image and I&apos;ll create it here. Once an image exists
                  you can say what to change.
                </p>
              </div>
            </div>
          )}

          <div className="mx-auto max-w-3xl px-6 space-y-0">
            {messages.map((m, i) => {
              const isPending = m.pending;
              const isEmpty = !m.content;
              const showTyping = streaming && isEmpty && i === messages.length - 1;
              const isUser = m.role === "user";

              return (
                <div
                  key={m.id ?? i}
                  className={cn(
                    "group py-5",
                    !isUser && "bg-gray-50/60 -mx-6 px-6 rounded-none"
                  )}
                >
                  <div className="flex gap-4 max-w-3xl mx-auto">
                    {/* Avatar */}
                    <div className="shrink-0 mt-0.5">
                      {isUser ? (
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-600 text-xs font-semibold text-white">
                          {me?.full_name?.[0]?.toUpperCase() ?? "U"}
                        </div>
                      ) : (
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white border border-gray-200">
                          <Sparkles className="h-4 w-4 text-emerald-500" />
                        </div>
                      )}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0 space-y-2">
                      <span className="text-sm font-semibold text-[#1f2937]">
                        {isUser ? "You" : "Assistant"}
                      </span>

                      {m.role === "assistant" && !isPending && m.content ? (
                        <div className="prose-chat text-[#1f2937]">
                          <Markdown>{m.content}</Markdown>
                        </div>
                      ) : (
                        <p className={cn(
                          "text-[15.5px] leading-relaxed whitespace-pre-wrap break-words text-[#1f2937]",
                          isPending && "italic text-[#6b7280]"
                        )}>
                          {m.content || (showTyping ? (
                            <span className="inline-flex gap-1">
                              <span className="animate-bounce">.</span>
                              <span className="animate-bounce [animation-delay:0.1s]">.</span>
                              <span className="animate-bounce [animation-delay:0.2s]">.</span>
                            </span>
                          ) : "")}
                        </p>
                      )}

                      {m.image_url && (
                        <div className="mt-3 max-w-md rounded-xl overflow-hidden border border-emerald-100 shadow-sm">
                          <AuthedImage src={m.image_url} alt={m.content || "Generated image"} />
                        </div>
                      )}

                      {m.file_url && (
                        <div className="mt-3 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 max-w-md shadow-sm">
                          <FileText className="h-8 w-8 text-emerald-600 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-[#1f2937] truncate">{m.file_name || "Download"}</p>
                            <p className="text-xs text-[#6b7280]">PowerPoint Presentation</p>
                          </div>
                          <a
                            href={m.file_url.startsWith("/api/") ? `${API_BASE}${m.file_url}` : m.file_url}
                            download
                            className="shrink-0 inline-flex h-9 w-9 items-center justify-center rounded-full bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                            title="Download"
                          >
                            <ArrowUp className="h-4 w-4 rotate-180" />
                          </a>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div ref={bottomRef} />
        </div>

        {/* Error banner */}
        {error && (
          <div className="mx-auto max-w-3xl w-full px-6 mb-2">
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <span className="flex-1">{error}</span>

              {showCreditRequest && (
                <button
                  onClick={() => void notifyAdminForCredits()}
                  disabled={requestingCredits}
                  className="shrink-0 ml-2 inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 transition-colors disabled:opacity-50"
                >
                  {requestingCredits ? "Sending..." : "Notify Admin"}
                </button>
              )}

              <button onClick={() => { setError(null); setShowCreditRequest(false); }} className="shrink-0 hover:opacity-70">
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        {/* Attachment chips */}
        {attachments.length > 0 && (
          <div className="mx-auto max-w-3xl w-full px-6 mb-2">
            <div className="flex flex-wrap gap-2">
              {attachments.map((a) => {
                const Icon = ATTACHMENT_ICONS[a.kind] ?? FileText;
                return (
                  <div
                    key={a.id}
                    className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm max-w-[280px]"
                    title={a.preview ?? a.filename}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                    <span className="truncate text-[#1f2937]">{a.filename}</span>
                    {a.chars_extracted > 0 && (
                      <span className="text-xs text-[#6b7280] shrink-0">
                        {a.chars_extracted.toLocaleString()} chars
                      </span>
                    )}
                    <button
                      type="button"
                      aria-label={`Remove ${a.filename}`}
                      onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                      className="shrink-0 inline-flex h-5 w-5 items-center justify-center rounded-full text-[#6b7280] hover:bg-emerald-100 hover:text-emerald-700 transition-colors"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Composer */}
        <div
          className={cn(
            "border-t border-emerald-100 bg-white px-4 py-3 md:px-6 transition-colors relative",
            dragOver && "bg-emerald-50 border-emerald-300"
          )}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          ref={composerRef}
        >
          {dragOver && (
            <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
              <div className="rounded-xl border-2 border-dashed border-emerald-400 bg-emerald-50/80 px-6 py-4 text-sm font-medium text-emerald-700 shadow-sm">
                Drop files here to attach
              </div>
            </div>
          )}
          <div className="mx-auto max-w-3xl">
            {/* Style Preset Chips */}
            <div className="mb-2 flex items-center gap-2 flex-wrap">
              <span className="text-xs text-[#6b7280] shrink-0">Styles:</span>
              
              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: cartoon style, playful, colorful, flat illustration, cute characters, educational infographic layout, suitable for teacher presentation, bright blue and yellow accents, clear step-by-step visuals]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 border border-emerald-200 hover:bg-emerald-100 hover:border-emerald-300 transition-colors"
                title="Playful cartoon style for younger grades"
              >
                <span>🎨</span>
                <span>Cartoonize</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: clean infographic layout, data visualization style, minimal icons, white background, professional color palette, structured sections, modern typography]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 border border-blue-200 hover:bg-blue-100 hover:border-blue-300 transition-colors"
                title="Professional charts and comparisons"
              >
                <span>📊</span>
                <span>Infographic</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: hand-drawn sketch style, marker on whiteboard, casual annotations, simple shapes, black ink on white paper, brainstorming aesthetic]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700 border border-gray-200 hover:bg-gray-100 hover:border-gray-300 transition-colors"
                title="Quick explanations and brainstorming"
              >
                <span>✏️</span>
                <span>Whiteboard</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: top-down map view, labeled regions, clean cartography style, muted earth tones, geographic markers, compass rose, educational atlas quality]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 border border-amber-200 hover:bg-amber-100 hover:border-amber-300 transition-colors"
                title="Geography and social studies maps"
              >
                <span>🗺️</span>
                <span>Map</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: board game layout, colorful tiles, interactive elements, child-friendly design, dice and cards visible, gamification aesthetic, vibrant colors]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-purple-50 px-3 py-1 text-xs font-medium text-purple-700 border border-purple-200 hover:bg-purple-100 hover:border-purple-300 transition-colors"
                title="Gamified lessons and quizzes"
              >
                <span>🧩</span>
                <span>Game Board</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: photorealistic, natural lighting, shallow depth of field, DSLR quality, high resolution, realistic textures, professional photography]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-3 py-1 text-xs font-medium text-slate-700 border border-slate-200 hover:bg-slate-100 hover:border-slate-300 transition-colors"
                title="Realistic photos for science and art"
              >
                <span>🖼️</span>
                <span>Realistic</span>
              </button>
            </div>

            <div className="relative flex items-end gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-3 shadow-sm focus-within:ring-2 focus-within:ring-emerald-500 focus-within:border-emerald-400 transition-shadow">
              <input
                ref={fileRef}
                type="file"
                multiple
                accept={ACCEPTED_UPLOADS}
                className="hidden"
                onChange={(e) => void onPickFiles(e.target.files)}
              />
              <button
                type="button"
                aria-label="Attach a file"
                title="Attach PDF, Word, PowerPoint, Excel, CSV, text or an image"
                disabled={uploading || streaming}
                onClick={() => fileRef.current?.click()}
                className="shrink-0 inline-flex h-9 w-9 items-center justify-center rounded-full text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700 transition-colors disabled:opacity-50 mb-[-2px]"
              >
                <Paperclip className="h-4 w-4" />
              </button>

              <textarea
                ref={textareaRef}
                rows={1}
                placeholder="Message the school assistant…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={streaming}
                className="flex-1 resize-none bg-transparent text-[15px] leading-relaxed placeholder:text-[#6b7280] text-[#1f2937] focus:outline-none disabled:opacity-50 py-1.5 max-h-[200px]"
              />

              <button
                onClick={() => streaming ? abortRef.current?.abort() : void send()}
                disabled={!streaming && !draft.trim()}
                aria-label={streaming ? "Stop generating" : "Send message"}
                className={cn(
                  "shrink-0 inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors mb-[-2px]",
                  streaming
                    ? "bg-red-500 text-white hover:bg-red-600"
                    : draft.trim()
                      ? "bg-emerald-600 text-white hover:bg-emerald-700"
                      : "bg-gray-100 text-[#6b7280] cursor-not-allowed"
                )}
              >
                {streaming ? <Square className="h-3.5 w-3.5" /> : <ArrowUp className="h-4 w-4" />}
              </button>
            </div>
            <p className="mt-2 text-center text-xs text-[#6b7280]">
              School AI can make mistakes. Verify important information.
            </p>
          </div>
        </div>
        {/* Floating New Chat button */}
        <button
          onClick={() => void openConversation(null)}
          className="fixed bottom-6 right-6 z-30 inline-flex h-14 w-14 items-center justify-center rounded-full bg-emerald-600 text-white shadow-lg hover:bg-emerald-700 hover:shadow-xl transition-all hover:scale-105 active:scale-95"
          title="New chat"
          aria-label="Start a new chat"
        >
          <Plus className="h-6 w-6" />
        </button>
      </main>
    </div>
  );
}

function BookOpenIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>
    </svg>
  );
}
