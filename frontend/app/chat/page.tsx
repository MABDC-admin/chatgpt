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
  fetchAttachments,
  getImageProviders,
  getToken,
  parseAttachmentMarker,
  uploadFile,
  type Attachment,
  IMAGE_MODELS,
  IMAGE_MODEL_LABELS,
  IMAGE_QUALITIES,
  IMAGE_SIZES,
  PPT_THEMES,
  type ChatMessage,
  type Conversation,
  type Credits,
  type Me,
} from "@/lib/api";
import { streamChat } from "@/lib/stream";
import { cn } from "@/lib/utils";
import ImageModelPicker from "@/components/ImageModelPicker";
import TextModelPicker, { type ReasoningEffort, type TextMode } from "@/components/TextModelPicker";
import { AlertCircle, Paperclip, ArrowUp, Square, X, FileText, Image as ImageIcon, Table as TableIcon, Sparkles, MessageCirclePlus, Copy, Check, RotateCcw, Pencil } from "lucide-react";

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
  const [imageSize, setImageSize] = useState<string>("1536x1024");
  // True when the user explicitly picked a ratio (via composer or inline buttons).
  // When false, the backend will show inline ratio buttons before generating.
  const [sizeExplicit, setSizeExplicit] = useState(false);
  // Selected style preset from the Image Studio ribbon. Defaults to "none"
  // (no style suffix); a proper style is chosen the first time the user
  // clicks a tile, then persisted per-viewer.
  // Set from /api/config/image-providers on mount so we hide models the
  // server can't reach (e.g. Qwen when NEXUM_API_KEY is unset).
  const [availableModels, setAvailableModels] = useState<string[]>(IMAGE_MODELS.slice());
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [useKnowledge, setUseKnowledge] = useState(false);
  // PPT / TEACHERDECK composer mode
  const [pptMode, setPptMode] = useState(false);
  const [pptTopic, setPptTopic] = useState("");
  const [pptSubject, setPptSubject] = useState("");
  const [pptLevel, setPptLevel] = useState("");
  const [pptDuration, setPptDuration] = useState("");
  const [pptSlides, setPptSlides] = useState("15");
  const [pptTheme, setPptTheme] = useState("emerald");
  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const [showCreditRequest, setShowCreditRequest] = useState(false);
  const [requestingCredits, setRequestingCredits] = useState(false);
  const [copiedMessageIndex, setCopiedMessageIndex] = useState<number | null>(null);

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

      // Collect every attachment id embedded in the user turns so we can
      // batch-fetch metadata once, then rebuild each message with its
      // parsed text + hydrated attachment list. Without this, the marker
      // comment shows up as literal text in the user bubble and the
      // uploaded picture disappears from the transcript on reload.
      const allIds: string[] = [];
      for (const m of detail.messages) {
        if (m.role === "user") {
          const { ids } = parseAttachmentMarker(m.content);
          allIds.push(...ids);
        }
      }
      const attachmentsById = new Map<string, Attachment>();
      if (allIds.length > 0) {
        const fetched = await fetchAttachments(allIds);
        for (const a of fetched) attachmentsById.set(a.id, a);
      }
      const rebuilt = detail.messages.map((m) => {
        if (m.role !== "user") return m;
        const { text, ids } = parseAttachmentMarker(m.content);
        const attachments = ids
          .map((id) => attachmentsById.get(id))
          .filter((a): a is Attachment => Boolean(a));
        return { ...m, content: text, attachments };
      });
      setMessages(rebuilt);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open that conversation");
    }
  }

  function startNewChat() {
    abortRef.current?.abort();
    setDraft("");
    setAttachments([]);
    void openConversation(null);
    window.setTimeout(() => textareaRef.current?.focus(), 0);
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
    let text = draft.trim();
    if (!text && !pptMode) return;
    if (streaming) return;

    // When PPT mode is active, format as TEACHERDECK command
    if (pptMode) {
      const parts: string[] = ["TEACHERDECK:"];
      if (pptTopic) parts.push(`Topic: ${pptTopic}`);
      if (pptSubject) parts.push(`Subject: ${pptSubject}`);
      if (pptLevel) parts.push(`Level: ${pptLevel}`);
      if (pptDuration) parts.push(`Duration: ${pptDuration} minutes`);
      if (pptSlides) parts.push(`Slides: ${pptSlides}`);
      if (pptTheme && pptTheme !== "emerald") parts.push(`Theme: ${pptTheme}`);
      const header = parts.join("\n");
      text = text ? `${header}\n\nAdditional instructions: ${text}` : header;
    }

    if (!text) return;

    setDraft("");
    setError(null);
    const sentWith = attachments;
    setAttachments([]);
    setMessages((prev) => [
      ...prev,
      // Attach the actual Attachment objects to the user message so its
      // thumbnails render immediately and survive re-renders. On history
      // reload we re-derive these from the "<!-- attachments: [...] -->"
      // marker in the stored content.
      { role: "user", content: text, attachments: sentWith },
      { role: "assistant", content: "" },
    ]);
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
          size: sizeExplicit ? imageSize : undefined,
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
          onRatioPrompt: ({ options, labels, message }) =>
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  content: message,
                  ratio_options: options,
                  ratio_labels: labels,
                  pending: false,
                };
              }
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
                  image_model: imageModel,
                };
              } else {
                // Bulk slides: append each new image as its own message
                next.push({
                  role: "assistant",
                  content: caption,
                  image_url: url,
                  image_model: imageModel,
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

  async function copyMessage(content: string, index: number) {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessageIndex(index);
      window.setTimeout(() => setCopiedMessageIndex(null), 1800);
    } catch {
      setError("Your browser could not copy that response.");
    }
  }

  function editUserMessage(content: string) {
    setDraft(content.replace(/\n?<!-- attachments: .*? -->/s, "").trim());
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }

  /** User picked an aspect ratio from the inline buttons. Resend the last user
   *  message with the chosen size so the backend generates immediately. */
  function pickRatio(size: string, label: string) {
    // Find the last user message to resend
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser || streaming) return;

    setImageSize(size);
    setSizeExplicit(true);

    // Remove the ratio_prompt assistant message and replace with pending placeholder
    setMessages((prev) => {
      const filtered = prev.filter((m) => !m.ratio_options);
      return [
        ...filtered,
        { role: "assistant" as const, content: `Generating ${label} image…`, pending: true },
      ];
    });

    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    const sentWith = lastUser.attachments ?? [];

    streamChat(
      {
        message: lastUser.content,
        conversation_id: activeId,
        mode: textMode,
        reasoning_effort: reasoningEffort,
        image_model: imageModel,
        size,
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
            if (last?.role === "assistant" && !last.image_url && (!last.content || last.pending)) {
              next[next.length - 1] = { role: "assistant", content: caption, image_url: url, image_model: imageModel };
            } else {
              next.push({ role: "assistant", content: caption, image_url: url, image_model: imageModel });
            }
            return next;
          }),
        onDelta: (piece) =>
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { role: "assistant", content: (last.pending ? "" : last.content) + piece };
            return next;
          }),
        onError: (message) => setError(message),
        onDone: () => {
          void refreshCredits();
          void refreshConversations();
        },
      },
      controller.signal,
    ).catch((err) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Failed");
    }).finally(() => {
      setStreaming(false);
      abortRef.current = null;
    });
  }

  async function regenerateLastAnswer() {
    if (!activeId || streaming || messages.length < 2) return;
    const answerIndex = messages.length - 1;
    const original = messages[answerIndex];
    if (original.role !== "assistant" || !original.content || original.image_url || original.file_url) return;

    setError(null);
    setMessages((prev) => prev.map((message, index) =>
      index === answerIndex ? { role: "assistant", content: "", pending: true } : message,
    ));
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    let receivedOutput = false;
    let failed = false;

    try {
      await streamChat(
        { mode: textMode, reasoning_effort: reasoningEffort },
        {
          onDelta: (piece) => {
            receivedOutput = true;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = {
                role: "assistant",
                content: (last.pending ? "" : last.content) + piece,
              };
              return next;
            });
          },
          onError: (message) => {
            failed = true;
            setError(message);
            if (!receivedOutput) {
              setMessages((prev) => prev.map((entry, index) => index === answerIndex ? original : entry));
            }
          },
          onDone: () => {
            void refreshCredits();
            void refreshConversations();
          },
        },
        controller.signal,
        `/conversations/${activeId}/regenerate`,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        failed = true;
        setError(err instanceof Error ? err.message : "The assistant could not regenerate that answer");
      }
    } finally {
      if (!receivedOutput && (controller.signal.aborted || failed)) {
        setMessages((prev) => prev.map((entry, index) => index === answerIndex ? original : entry));
      }
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
        onSelect={(id) => id === null ? startNewChat() : void openConversation(id)}
        onSearch={searchConversations}
        onRename={(id, title) => void renameConversation(id, title)}
        onArchive={(id) => void archiveConversation(id)}
        onDelete={(id) => void deleteConversation(id)}
      />

      <main className="flex flex-1 flex-col min-w-0 relative">
        {/* Compact command bar: chat controls remain visible when the sidebar is closed. */}
        <div className="flex items-center gap-2 border-b border-emerald-100 bg-white px-4 py-2.5 md:px-6">
          <div className="hidden min-w-0 items-center gap-2 pr-2 sm:flex">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-sky-100 bg-sky-50 text-sky-700">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-[#1f2937]">MABDC Chat</p>
              <p className="text-[11px] text-[#6b7280]">School workspace</p>
            </div>
          </div>
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
          <button
            type="button"
            onClick={() => setPptMode((v) => !v)}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors",
              pptMode
                ? "border-amber-400 bg-amber-50 text-amber-700 ring-2 ring-amber-200"
                : "border-gray-200 bg-white text-[#6b7280] hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700"
            )}
            title="Toggle presentation mode (TEACHERDECK)"
          >
            <TableIcon className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">PPT</span>
          </button>
          <ImageModelPicker
            availableModels={availableModels}
            model={imageModel}
            quality={imageQuality}
            size={imageSize}
            onChange={({ model, quality, size }) => {
              setImageModel(model);
              setImageQuality(quality);
              setImageSize(size);
              setSizeExplicit(true);
              try {
                window.localStorage.setItem("teacherai.imageModel", model);
                window.localStorage.setItem("teacherai.imageQuality", quality);
                window.localStorage.setItem("teacherai.imageSize", size);
              } catch { /* private mode */ }
            }}
          />
          <label className="ml-auto hidden cursor-pointer select-none items-center gap-2 text-sm text-[#6b7280] lg:inline-flex">
            <input
              type="checkbox"
              checked={useKnowledge}
              onChange={(e) => setUseKnowledge(e.target.checked)}
              className="h-4 w-4 rounded border-emerald-300 text-emerald-600 accent-emerald-600 cursor-pointer"
            />
            <BookOpenIcon className="h-3.5 w-3.5" />
            Knowledge base
          </label>
          <button
            type="button"
            onClick={startNewChat}
            className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg bg-emerald-600 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2"
            title="Start a new chat"
          >
            <MessageCirclePlus className="h-4 w-4" />
            <span className="hidden sm:inline">New chat</span>
          </button>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto py-6">
          {messages.length === 0 && (
            <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-emerald-100 bg-emerald-50">
                <Sparkles className="h-6 w-6 text-emerald-600" />
              </div>
              <div className="mt-4 space-y-1.5">
                <h2 className="text-xl font-semibold text-[#1f2937]">What are we working on?</h2>
                <p className="max-w-md text-sm leading-relaxed text-[#6b7280]">
                  Plan, explain, create, or work from a document in the shared knowledge base.
                </p>
              </div>
              <div className="mt-6 grid w-full max-w-2xl gap-2 text-left sm:grid-cols-3">
                {[
                  ["Plan a lesson", "Create a differentiated lesson plan for..."],
                  ["Explain a topic", "Explain this topic at a Grade 6 level: ..."],
                  ["Create a visual", "Create an educational visual about..."],
                ].map(([label, prompt]) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => {
                      setDraft(prompt);
                      window.setTimeout(() => textareaRef.current?.focus(), 0);
                    }}
                    className="rounded-lg border border-gray-200 bg-white px-3 py-3 text-left transition-colors hover:border-emerald-300 hover:bg-emerald-50"
                  >
                    <span className="block text-sm font-medium text-[#1f2937]">{label}</span>
                    <span className="mt-1 block text-xs leading-relaxed text-[#6b7280]">{prompt}</span>
                  </button>
                ))}
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

                      {/* Files the user attached to THIS turn: thumbnails for
                          images, chip for anything else. Same visual grammar
                          as the composer preview, so a photo stays visible in
                          the transcript instead of vanishing after Enter. */}
                      {isUser && m.attachments && m.attachments.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {m.attachments.map((a) => {
                            if (a.kind === "image") {
                              return (
                                <div
                                  key={a.id}
                                  className="h-20 w-20 shrink-0 overflow-hidden rounded-lg border border-emerald-200 bg-emerald-50 shadow-sm"
                                  title={a.filename}
                                >
                                  <AuthedImage
                                    src={`/api/files/download/${a.id}`}
                                    alt={a.filename}
                                    className="h-full w-full object-cover"
                                  />
                                </div>
                              );
                            }
                            const Icon = ATTACHMENT_ICONS[a.kind] ?? FileText;
                            return (
                              <div
                                key={a.id}
                                className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs max-w-[220px]"
                                title={a.filename}
                              >
                                <Icon className="h-3 w-3 shrink-0 text-emerald-600" />
                                <span className="truncate text-[#1f2937]">{a.filename}</span>
                              </div>
                            );
                          })}
                        </div>
                      )}

                      {m.image_url && (
                        <div className="mt-3 max-w-md">
                          <div className="rounded-xl overflow-hidden border border-emerald-100 shadow-sm">
                            <AuthedImage src={m.image_url} alt={m.content || "Generated image"} />
                          </div>
                          {m.image_model && (
                            <p className="mt-1.5 text-[11px] text-[#6b7280] flex items-center gap-1">
                              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
                              Generated with {IMAGE_MODEL_LABELS[m.image_model] || m.image_model}
                            </p>
                          )}
                        </div>
                      )}

                      {m.file_url && (
                        <div className="mt-3 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 max-w-md shadow-sm">
                          <FileText className="h-8 w-8 text-emerald-600 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-[#1f2937] truncate">{m.file_name || "Download"}</p>
                            <p className="text-xs text-[#6b7280]">PowerPoint Presentation</p>
                          </div>
                          <button
                            type="button"
                            onClick={async () => {
                              const url = m.file_url!.startsWith("/api/") ? `${API_BASE}${m.file_url}` : m.file_url!;
                              try {
                                const token = getToken();
                                const res = await fetch(url, {
                                  headers: token ? { Authorization: `Bearer ${token}` } : {},
                                });
                                if (!res.ok) throw new Error(String(res.status));
                                const blob = await res.blob();
                                const blobUrl = URL.createObjectURL(blob);
                                const a = document.createElement("a");
                                a.href = blobUrl;
                                a.download = m.file_name || "presentation.pptx";
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                                URL.revokeObjectURL(blobUrl);
                              } catch (err) {
                                setError(`Download failed: ${err instanceof Error ? err.message : "unknown error"}`);
                              }
                            }}
                            className="shrink-0 inline-flex h-9 w-9 items-center justify-center rounded-full bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                            title="Download"
                          >
                            <ArrowUp className="h-4 w-4 rotate-180" />
                          </button>
                        </div>
                      )}

                      {/* Inline aspect-ratio picker shown when backend asks */}
                      {m.ratio_options && m.ratio_options.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {m.ratio_options.map((opt, idx) => {
                            const label = m.ratio_labels?.[idx] ?? opt;
                            const shapes: Record<string, { w: number; h: number }> = {
                              "1024x1024": { w: 20, h: 20 },
                              "1536x1024": { w: 26, h: 18 },
                              "1024x1536": { w: 18, h: 26 },
                            };
                            const shape = shapes[opt] ?? shapes["1024x1024"];
                            return (
                              <button
                                key={opt}
                                type="button"
                                onClick={() => pickRatio(opt, label)}
                                disabled={streaming}
                                className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-sm font-medium text-[#1f2937] hover:border-emerald-400 hover:bg-emerald-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                <span
                                  className="rounded-sm border border-emerald-300 bg-emerald-50"
                                  style={{ width: shape.w, height: shape.h }}
                                />
                                {label}
                              </button>
                            );
                          })}
                        </div>
                      )}

                      {!isPending && m.content && (
                        <div className="flex items-center gap-1 pt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                          {isUser ? (
                            <button
                              type="button"
                              onClick={() => editUserMessage(m.content)}
                              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-100 hover:text-emerald-700"
                              title="Edit and resend"
                              aria-label="Edit and resend"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={() => void copyMessage(m.content, i)}
                                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-100 hover:text-emerald-700"
                                title="Copy response"
                                aria-label="Copy response"
                              >
                                {copiedMessageIndex === i ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                              </button>
                              {i === messages.length - 1 && !m.image_url && !m.file_url && (
                                <button
                                  type="button"
                                  onClick={() => void regenerateLastAnswer()}
                                  disabled={streaming}
                                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-100 hover:text-emerald-700 disabled:opacity-50"
                                  title="Regenerate response"
                                  aria-label="Regenerate response"
                                >
                                  <RotateCcw className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </>
                          )}
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

        {/* Attachment previews: images render as thumbnail tiles (a real
            preview of what was uploaded, ChatGPT-style), every other type
            keeps the pill chip with its file icon. */}
        {attachments.length > 0 && (
          <div className="mx-auto max-w-3xl w-full px-6 mb-2">
            <div className="flex flex-wrap gap-2 items-start">
              {attachments.map((a) => {
                const remove = () =>
                  setAttachments((prev) => prev.filter((x) => x.id !== a.id));

                if (a.kind === "image") {
                  return (
                    <div
                      key={a.id}
                      className="group relative h-16 w-16 shrink-0 overflow-hidden rounded-lg border border-emerald-200 bg-emerald-50 shadow-sm"
                      title={a.filename}
                    >
                      <AuthedImage
                        src={`/api/files/download/${a.id}`}
                        alt={a.filename}
                        className="h-full w-full object-cover"
                      />
                      <button
                        type="button"
                        aria-label={`Remove ${a.filename}`}
                        onClick={remove}
                        className="absolute right-0.5 top-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                }

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
                      onClick={remove}
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
            {/* Image-oriented prompt helpers stay compact until the user needs them.
                In PPT mode, replaced with TEACHERDECK structured input fields. */}
            {pptMode ? (
              <div className="mb-2 flex items-center gap-2 overflow-x-auto pb-0.5 flex-wrap">
                <span className="shrink-0 text-xs font-semibold text-amber-700 uppercase tracking-wider">📊 TeacherDeck</span>
                <input
                  type="text"
                  placeholder="Topic (e.g. Photosynthesis)"
                  value={pptTopic}
                  onChange={(e) => setPptTopic(e.target.value)}
                  disabled={streaming}
                  className="h-7 min-w-[160px] rounded-md border border-amber-200 bg-amber-50 px-2.5 text-xs text-[#1f2937] placeholder:text-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
                />
                <input
                  type="text"
                  placeholder="Subject (e.g. Science)"
                  value={pptSubject}
                  onChange={(e) => setPptSubject(e.target.value)}
                  disabled={streaming}
                  className="h-7 min-w-[120px] rounded-md border border-amber-200 bg-amber-50 px-2.5 text-xs text-[#1f2937] placeholder:text-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
                />
                <input
                  type="text"
                  placeholder="Level/Grade (e.g. 7)"
                  value={pptLevel}
                  onChange={(e) => setPptLevel(e.target.value)}
                  disabled={streaming}
                  className="h-7 w-28 shrink-0 rounded-md border border-amber-200 bg-amber-50 px-2.5 text-xs text-[#1f2937] placeholder:text-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
                />
                <input
                  type="text"
                  placeholder="Duration (e.g. 50)"
                  value={pptDuration}
                  onChange={(e) => setPptDuration(e.target.value)}
                  disabled={streaming}
                  className="h-7 w-28 shrink-0 rounded-md border border-amber-200 bg-amber-50 px-2.5 text-xs text-[#1f2937] placeholder:text-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
                />
                <div className="flex items-center gap-1">
                  <label className="text-xs text-amber-600">Slides:</label>
                  <input
                    type="number"
                    min={5}
                    max={40}
                    value={pptSlides}
                    onChange={(e) => setPptSlides(e.target.value)}
                    disabled={streaming}
                    className="h-7 w-16 shrink-0 rounded-md border border-amber-200 bg-amber-50 px-2 text-xs text-[#1f2937] focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
                  />
                </div>
                <div className="flex items-center gap-1 ml-auto">
                  <span className="text-xs text-amber-600 mr-1">Theme:</span>
                  {PPT_THEMES.map((th) => {
                    const active = pptTheme === th.id;
                    return (
                      <button
                        key={th.id}
                        type="button"
                        onClick={() => setPptTheme(th.id)}
                        disabled={streaming}
                        title={th.label}
                        className={cn(
                          "shrink-0 flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-medium transition-all disabled:opacity-50",
                          active
                            ? "border-amber-400 bg-amber-100 text-amber-800 ring-1 ring-amber-300"
                            : "border-gray-200 bg-white text-gray-500 hover:border-amber-300"
                        )}
                      >
                        <span className="flex gap-0.5">
                          <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: th.primary }} />
                          <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: th.accent }} />
                        </span>
                        <span className="hidden sm:inline">{th.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
            <div className="mb-2 flex items-center gap-2 overflow-x-auto pb-0.5">
              <span className="shrink-0 text-xs text-[#6b7280]">Image styles</span>
              
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

              {/* Non-redundant additions: warm storybook (early years literacy),
                  labelled science diagram (science topics -- distinct from the
                  Infographic tile's "clean data viz" tone), sepia historical
                  aesthetic (history/values), and narrative comic panels
                  (language arts, biographies, story sequences). */}
              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: warm children's storybook illustration, soft pencil and watercolor textures, gentle color palette, expressive characters, picture-book composition, suitable for early years reading materials]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 border border-rose-200 hover:bg-rose-100 hover:border-rose-300 transition-colors"
                title="Storybook illustration for early years"
              >
                <span>📚</span>
                <span>Storybook</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: labeled scientific diagram, cross-section view with callout arrows and annotation text, clean line art on white background, textbook illustration quality, biology or physics reference style]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-3 py-1 text-xs font-medium text-teal-700 border border-teal-200 hover:bg-teal-100 hover:border-teal-300 transition-colors"
                title="Labeled scientific cross-sections and reference diagrams"
              >
                <span>🔬</span>
                <span>Science</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: sepia-toned historical illustration, aged paper texture, engraved lithograph look, muted browns and creams, 19th-century textbook aesthetic, subtle grain, timeless composition]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-yellow-50 px-3 py-1 text-xs font-medium text-yellow-800 border border-yellow-200 hover:bg-yellow-100 hover:border-yellow-300 transition-colors"
                title="Vintage textbook look for history topics"
              >
                <span>📜</span>
                <span>Vintage</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  const suffix = " [Style: comic book panel layout, sequential storytelling with 3 to 4 panels, clean linework with bold outlines, speech bubbles allowed, expressive characters, dynamic narrative composition]";
                  if (!draft.includes(suffix)) setDraft(draft + suffix);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 border border-sky-200 hover:bg-sky-100 hover:border-sky-300 transition-colors"
                title="Comic-strip storytelling for language arts and history"
              >
                <span>🎬</span>
                <span>Comic Strip</span>
              </button>
            </div>
            )}

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
                placeholder={pptMode ? "Describe your lesson or leave blank to use fields above…" : "Message the school assistant…"}
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
