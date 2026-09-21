"use client";

import { useEffect, useRef, useState } from "react";
import { BrainCircuit, Check, ChevronDown, Gauge, Sparkles } from "lucide-react";

export type TextMode = "auto" | "luna" | "terra" | "sol";
export type ReasoningEffort = "low" | "medium" | "high";

type Props = {
  mode: TextMode;
  reasoningEffort: ReasoningEffort;
  onChange: (next: { mode: TextMode; reasoningEffort: ReasoningEffort }) => void;
};

const MODES: Array<{
  id: TextMode;
  label: string;
  description: string;
  icon: typeof Sparkles;
}> = [
  { id: "auto", label: "Auto", description: "Routes by task complexity", icon: Sparkles },
  { id: "luna", label: "Luna", description: "Fast everyday help", icon: Gauge },
  { id: "terra", label: "Thinking", description: "Stronger analysis", icon: BrainCircuit },
  { id: "sol", label: "Pro", description: "Complex work", icon: BrainCircuit },
];

const EFFORTS: Array<{ id: ReasoningEffort; label: string; description: string }> = [
  { id: "low", label: "Quick", description: "Least reasoning" },
  { id: "medium", label: "Balanced", description: "Everyday depth" },
  { id: "high", label: "Deep", description: "More careful reasoning" },
];

export default function TextModelPicker({ mode, reasoningEffort, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = MODES.find((item) => item.id === mode) ?? MODES[0];
  const SelectedIcon = selected.icon;

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-2 rounded-lg border border-sky-200 bg-white px-3 py-1.5 text-sm text-[#1f2937] transition-colors hover:border-sky-400 hover:bg-sky-50 focus:outline-none focus:ring-2 focus:ring-sky-500"
        aria-label="Text model and reasoning settings"
        aria-expanded={open}
      >
        <SelectedIcon className="h-3.5 w-3.5 text-sky-600" />
        <span className="font-medium">{selected.label}</span>
        <span className="text-[#6b7280]">·</span>
        <span className="text-[#6b7280]">{EFFORTS.find((item) => item.id === reasoningEffort)?.label}</span>
        <ChevronDown className={`h-3.5 w-3.5 text-[#6b7280] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-[calc(100%+8px)] z-30 w-[360px] max-w-[calc(100vw-32px)] space-y-4 rounded-xl border border-sky-100 bg-white p-4 shadow-xl">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#374151]">Text model</div>
            <div className="grid grid-cols-2 gap-2">
              {MODES.map((item) => {
                const Icon = item.icon;
                const selectedMode = mode === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onChange({ mode: item.id, reasoningEffort })}
                    className={`rounded-lg border p-3 text-left transition-all ${
                      selectedMode
                        ? "border-sky-500 bg-sky-50 ring-2 ring-sky-200"
                        : "border-gray-200 hover:border-sky-300 hover:bg-sky-50/40"
                    }`}
                    aria-pressed={selectedMode}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#1f2937]">
                        <Icon className="h-3.5 w-3.5 text-sky-600" />
                        {item.label}
                      </span>
                      {selectedMode && <Check className="h-4 w-4 text-sky-600" />}
                    </div>
                    <div className="mt-1 text-[11px] text-[#6b7280]">{item.description}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#374151]">Reasoning depth</div>
            <div className="grid grid-cols-3 gap-1.5 rounded-lg bg-gray-100 p-1">
              {EFFORTS.map((item) => {
                const selectedEffort = reasoningEffort === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onChange({ mode, reasoningEffort: item.id })}
                    title={item.description}
                    className={`rounded-md py-1.5 text-sm transition-colors ${
                      selectedEffort
                        ? "bg-white font-medium text-[#1f2937] shadow-sm"
                        : "text-[#6b7280] hover:text-[#1f2937]"
                    }`}
                    aria-pressed={selectedEffort}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>
            <div className="mt-1.5 text-[11px] text-[#6b7280]">
              {EFFORTS.find((item) => item.id === reasoningEffort)?.description}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
