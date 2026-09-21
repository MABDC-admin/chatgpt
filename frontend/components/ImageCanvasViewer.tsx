"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Download, Maximize2, Minus, Plus, X } from "lucide-react";

type Props = {
  images: string[];
  index: number;
  labels?: string[];
  onClose: () => void;
  onNavigate: (index: number) => void;
};

export default function ImageCanvasViewer({ images, index, labels, onClose, onNavigate }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0, offsetX: 0, offsetY: 0 });
  const imageCache = useRef(new Map<string, HTMLImageElement>());
  const currentSrc = images[index];

  const loadImage = useCallback(async (src: string): Promise<HTMLImageElement> => {
    const cached = imageCache.current.get(src);
    if (cached?.complete && cached.naturalWidth > 0) return cached;

    // Authenticated media routes (/api/images/file/... and /api/files/...)
    // return 401 to a plain image request because <img>/new Image() cannot
    // carry an Authorization header. Fetch with the bearer token, then hand
    // the bytes to Image() as a blob URL. External URLs still load directly.
    const needsAuth = src.startsWith("/api/") || src.startsWith("/media/");
    let objectUrl: string | null = null;
    let effectiveSrc = src;
    if (needsAuth) {
      const res = await fetch(src, { headers: getTokenHeaders() });
      if (!res.ok) throw new Error(`Image failed: ${res.status}`);
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      effectiveSrc = objectUrl;
    }

    return new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      // No crossOrigin: blob URLs are same-origin, and the pre-auth fetch has
      // already validated access.
      img.onload = () => {
        imageCache.current.set(src, img);
        resolve(img);
      };
      img.onerror = () => {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        reject(new Error("Could not decode image"));
      };
      img.src = effectiveSrc;
    });
  }, []);

  const draw = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !currentSrc) return;
    try {
      const img = await loadImage(currentSrc);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#f8fafc";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const fit = Math.min(canvas.width / img.width, canvas.height / img.height);
      const baseW = img.width * fit;
      const baseH = img.height * fit;
      const w = baseW * scale;
      const h = baseH * scale;
      const x = (canvas.width - w) / 2 + offset.x * dpr;
      const y = (canvas.height - h) / 2 + offset.y * dpr;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(img, x, y, w, h);
    } catch {
      /* handled by UI */
    }
  }, [currentSrc, loadImage, offset, scale]);

  useEffect(() => {
    void draw();
  }, [draw]);

  useEffect(() => {
    const handleResize = () => void draw();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [draw]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft" && index > 0) onNavigate(index - 1);
      if (event.key === "ArrowRight" && index < images.length - 1) onNavigate(index + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [images.length, index, onClose, onNavigate]);

  const resetFit = () => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  };

  const actualSize = async () => {
    const img = await loadImage(currentSrc);
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    setScale((img.width / rect.width) * dpr);
    setOffset({ x: 0, y: 0 });
  };

  const toggleFullscreen = () => {
    const el = document.getElementById("image-canvas-viewer");
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen();
  };

  const download = async () => {
    const res = await fetch(currentSrc, { headers: getTokenHeaders() });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = sanitizeFilename(labels?.[index] ?? `MABDC-image-${Date.now()}.png`);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const startDrag = (clientX: number, clientY: number) => {
    setDragging(true);
    dragStart.current = { x: clientX, y: clientY, offsetX: offset.x, offsetY: offset.y };
  };

  const moveDrag = (clientX: number, clientY: number) => {
    if (!dragging) return;
    setOffset({
      x: dragStart.current.offsetX + (clientX - dragStart.current.x),
      y: dragStart.current.offsetY + (clientY - dragStart.current.y),
    });
  };

  const zoomBy = (factor: number) => setScale((s) => Math.min(Math.max(s * factor, 0.25), 8));

  return (
    <div id="image-canvas-viewer" className="fixed inset-0 z-[100] flex flex-col bg-slate-950/95 backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3 text-white">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{labels?.[index] ?? `Image ${index + 1} of ${images.length}`}</p>
          <p className="text-xs text-white/60">Zoom {(scale * 100).toFixed(0)}%</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1">
          <ToolbarButton label="Zoom out" onClick={() => zoomBy(0.8)}><Minus className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Zoom in" onClick={() => zoomBy(1.25)}><Plus className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Fit to screen" onClick={resetFit}><Maximize2 className="h-4 w-4 rotate-45" /></ToolbarButton>
          <ToolbarButton label="Actual size" onClick={() => void actualSize()}><span className="text-xs font-bold">1:1</span></ToolbarButton>
          <ToolbarButton label="Full screen" onClick={toggleFullscreen}><Maximize2 className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Download" onClick={() => void download()}><Download className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Previous" disabled={index === 0} onClick={() => onNavigate(index - 1)}><ChevronLeft className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Next" disabled={index >= images.length - 1} onClick={() => onNavigate(index + 1)}><ChevronRight className="h-4 w-4" /></ToolbarButton>
          <ToolbarButton label="Close" onClick={onClose}><X className="h-4 w-4" /></ToolbarButton>
        </div>
      </div>
      <canvas
        ref={canvasRef}
        className="min-h-0 w-full flex-1 cursor-grab touch-none active:cursor-grabbing"
        onMouseDown={(e) => startDrag(e.clientX, e.clientY)}
        onMouseMove={(e) => moveDrag(e.clientX, e.clientY)}
        onMouseUp={() => setDragging(false)}
        onMouseLeave={() => setDragging(false)}
        onTouchStart={(e) => { const t = e.touches[0]; startDrag(t.clientX, t.clientY); }}
        onTouchMove={(e) => { const t = e.touches[0]; moveDrag(t.clientX, t.clientY); }}
        onTouchEnd={() => setDragging(false)}
        onWheel={(e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.1 : 0.9); }}
      />
    </div>
  );
}

function ToolbarButton({ children, label, onClick, disabled }: { children: React.ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick} className="inline-flex h-9 min-w-9 items-center justify-center rounded-lg border border-white/15 bg-white/10 px-2 text-white transition hover:bg-white/20 disabled:opacity-35">
      {children}
    </button>
  );
}

function getTokenHeaders(): Record<string, string> {
  try {
    const token = window.localStorage.getItem("teacherai.token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

function sanitizeFilename(value: string): string {
  const clean = value.replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  return `${clean || "MABDC-image"}${clean.match(/\.[a-z0-9]{2,5}$/i) ? "" : ".png"}`;
}
