"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ImageIcon, Zap, Gem, Check } from "lucide-react";
import {
  IMAGE_MODELS,
  IMAGE_MODEL_INFO,
  IMAGE_QUALITIES,
  IMAGE_SIZES,
  imageCostHint,
} from "@/lib/api";

/** Optional filter of model IDs to display. Falls back to the full catalogue. */
type ExtraProps = { availableModels?: readonly string[] };

/**
 * Premium composer control for image generation settings.
 *
 * Replaces the three flat <select> boxes with a single trigger that shows the
 * current choice ("Flare · Medium · Portrait · ~2.0¢") and opens a rich panel
 * with model tiles, a quality pill, aspect-ratio icons at real proportions,
 * and a live cost preview. Collapses back to the strip on any outside click.
 *
 * Fully controlled: the composer owns the state and just passes setters in.
 */
type Props = {
  model: string;
  quality: string;
  size: string;
  onChange: (next: { model: string; quality: string; size: string }) => void;
} & ExtraProps;

const QUALITY_META: Record<string, { label: string; hint: string; icon: React.ReactNode }> = {
  low:    { label: "Draft",    hint: "Fast + cheap, drafts",       icon: <Zap className="h-3.5 w-3.5" /> },
  medium: { label: "Standard", hint: "Balanced everyday quality",  icon: <ImageIcon className="h-3.5 w-3.5" /> },
  high:   { label: "Premium",  hint: "Highest fidelity, print",    icon: <Gem className="h-3.5 w-3.5" /> },
};

/** Show aspect ratios as visual rectangles so the choice is obvious at a glance. */
const RATIO_SHAPES: Record<string, { w: number; h: number; nickname: string }> = {
  "1024x1024": { w: 20, h: 20, nickname: "Square" },
  "1536x1024": { w: 24, h: 16, nickname: "Landscape" },
  "1024x1536": { w: 16, h: 24, nickname: "Portrait" },
};

export default function ImageModelPicker({
  model, quality, size, onChange, availableModels,
}: Props) {
  const modelList = (availableModels && availableModels.length > 0)
    ? availableModels
    : IMAGE_MODELS;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Any click outside the popover dismisses it.
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  const info = IMAGE_MODEL_INFO[model] ?? IMAGE_MODEL_INFO[modelList[0]];
  const ratio = RATIO_SHAPES[size] ?? RATIO_SHAPES["1024x1024"];
  // Subscription-priced providers (Nexum) show a flat label; token-billed
  // ones show the estimated cents.
  const cost = info?.billing ?? imageCostHint(model, quality, size);

  const set = (patch: Partial<{ model: string; quality: string; size: string }>) =>
    onChange({ model, quality, size, ...patch });

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-1.5 text-sm text-[#1f2937] hover:border-emerald-400 hover:bg-emerald-50 transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500"
        aria-label="Image generation settings"
        aria-expanded={open}
      >
        <ImageIcon className="h-3.5 w-3.5 text-emerald-600" />
        <span className="font-medium">{info.label}</span>
        <span className="text-[#6b7280]">·</span>
        <span className="text-[#6b7280]">{QUALITY_META[quality]?.label ?? quality}</span>
        <span className="text-[#6b7280]">·</span>
        <span className="text-[#6b7280]">{ratio.nickname}</span>
        <span className="text-emerald-700 font-medium">{cost}</span>
        <ChevronDown className={`h-3.5 w-3.5 text-[#6b7280] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-[calc(100%+8px)] z-30 w-[420px] max-w-[calc(100vw-32px)] rounded-xl border border-emerald-100 bg-white shadow-xl p-4 space-y-4">
          {/* Model tiles */}
          <div>
            <div className="text-xs font-semibold text-[#374151] uppercase tracking-wider mb-2">Model</div>
            <div className={`grid gap-2 ${modelList.length >= 3 ? "grid-cols-3" : "grid-cols-2"}`}>
              {modelList.map((m) => {
                const meta = IMAGE_MODEL_INFO[m];
                const selected = model === m;
                return (
                  <button
                    key={m}
                    type="button"
                    onClick={() => set({ model: m })}
                    className={`text-left rounded-lg border p-3 transition-all ${
                      selected
                        ? "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-200"
                        : "border-gray-200 hover:border-emerald-300 hover:bg-emerald-50/40"
                    }`}
                    aria-pressed={selected}
                  >
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold text-[#1f2937]">{meta.label}</div>
                      {selected && <Check className="h-4 w-4 text-emerald-600" />}
                    </div>
                    <div className="text-[11px] text-[#6b7280] mt-0.5">{meta.tagline}</div>
                    <div className="mt-2 space-y-1">
                      <ScoreBar label="Speed"   value={meta.speedScore} />
                      <ScoreBar label="Quality" value={meta.qualityScore} />
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Quality pill */}
          <div>
            <div className="text-xs font-semibold text-[#374151] uppercase tracking-wider mb-2">Quality</div>
            <div className="grid grid-cols-3 gap-1.5 rounded-lg bg-gray-100 p-1">
              {IMAGE_QUALITIES.map((q) => {
                const meta = QUALITY_META[q];
                const selected = quality === q;
                return (
                  <button
                    key={q}
                    type="button"
                    onClick={() => set({ quality: q })}
                    title={meta.hint}
                    className={`flex items-center justify-center gap-1.5 rounded-md py-1.5 text-sm transition-colors ${
                      selected
                        ? "bg-white text-[#1f2937] shadow-sm font-medium"
                        : "text-[#6b7280] hover:text-[#1f2937]"
                    }`}
                    aria-pressed={selected}
                  >
                    {meta.icon}
                    {meta.label}
                  </button>
                );
              })}
            </div>
            <div className="text-[11px] text-[#6b7280] mt-1.5">
              {QUALITY_META[quality]?.hint}
            </div>
          </div>

          {/* Aspect ratio -- shown as scaled rectangles so the choice reads at a glance */}
          <div>
            <div className="text-xs font-semibold text-[#374151] uppercase tracking-wider mb-2">Aspect ratio</div>
            <div className="grid grid-cols-3 gap-2">
              {IMAGE_SIZES.map((s) => {
                const shape = RATIO_SHAPES[s.value] ?? RATIO_SHAPES["1024x1024"];
                const selected = size === s.value;
                return (
                  <button
                    key={s.value}
                    type="button"
                    onClick={() => set({ size: s.value })}
                    className={`flex flex-col items-center gap-1.5 rounded-lg border py-2.5 transition-all ${
                      selected
                        ? "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-200"
                        : "border-gray-200 hover:border-emerald-300 hover:bg-emerald-50/40"
                    }`}
                    aria-pressed={selected}
                    title={s.label}
                  >
                    <div
                      className={`rounded-sm border ${selected ? "border-emerald-500 bg-white" : "border-gray-300 bg-gray-50"}`}
                      style={{ width: shape.w + "px", height: shape.h + "px" }}
                    />
                    <div className="text-[11px] font-medium text-[#374151]">{shape.nickname}</div>
                    <div className="text-[10px] text-[#6b7280]">{s.value}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Cost line -- restates what the trigger shows. Reads "Included in
              Nexum plan" for subscription-billed providers, cents otherwise. */}
          <div className="flex items-center justify-between rounded-lg bg-emerald-50 border border-emerald-100 px-3 py-2">
            <div className="text-xs text-[#374151]">
              {info?.billing ? "Billing" : "Estimated cost per image"}
            </div>
            <div className="text-sm font-semibold text-emerald-700">{cost || "—"}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="w-12 text-[#6b7280]">{label}</span>
      <div className="flex-1 flex gap-0.5">
        {[1, 2, 3, 4, 5].map((n) => (
          <div
            key={n}
            className={`h-1.5 flex-1 rounded-full ${
              n <= value ? "bg-emerald-500" : "bg-gray-200"
            }`}
          />
        ))}
      </div>
    </div>
  );
}
