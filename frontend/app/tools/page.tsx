"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import Markdown from "@/components/Markdown";
import { api, downloadFile, getToken, type Credits, type Me } from "@/lib/api";
import { streamTool } from "@/lib/stream";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { AlertCircle, Download, FileText, Loader2, Sparkles } from "lucide-react";

type ToolType = "lesson-plan" | "assessment" | "worksheet" | "slides";

const ASSESSMENT_TYPES = ["quiz", "test", "rubric", "exit-ticket"];
const WORKSHEET_TYPES = ["practice", "review", "homework", "activity"];

const TOOL_LABELS: Record<ToolType, string> = {
  "lesson-plan": "Lesson Plan",
  assessment: "Assessment",
  worksheet: "Worksheet",
  slides: "Slide Deck",
};

export default function ToolsPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [credits, setCredits] = useState<Credits | null>(null);
  const [tool, setTool] = useState<ToolType>("lesson-plan");
  const [topic, setTopic] = useState("");
  const [grade, setGrade] = useState("");
  const [duration, setDuration] = useState("60");
  const [standards, setStandards] = useState("");
  const [assessmentType, setAssessmentType] = useState("quiz");
  const [questionCount, setQuestionCount] = useState("10");
  const [worksheetType, setWorksheetType] = useState("practice");
  const [slideCount, setSlideCount] = useState("10");
  const [notes, setNotes] = useState("");
  const [output, setOutput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cost, setCost] = useState<number | null>(null);
  const outputRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const [user, credit] = await Promise.all([
      api<Me>("/auth/me"),
      api<Credits>("/auth/me/credits"),
    ]);
    setMe(user);
    setCredits(credit);
  }, []);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    load().catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [router, load]);

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [output]);

  function buildBody(): Record<string, unknown> {
    const base = { topic, grade_level: grade, additional_notes: notes };
    switch (tool) {
      case "lesson-plan":
        return { ...base, duration_minutes: Number(duration), standards };
      case "assessment":
        return { ...base, assessment_type: assessmentType, question_count: Number(questionCount) };
      case "worksheet":
        return { ...base, worksheet_type: worksheetType };
      case "slides":
        return { ...base, slide_count: Number(slideCount) };
    }
  }

  async function generate() {
    if (!topic.trim() || !grade.trim() || streaming) return;
    setOutput("");
    setError(null);
    setNotice(null);
    setCost(null);
    setStreaming(true);

    try {
      await streamTool(tool, buildBody(), {
        onDelta: (text) => setOutput((prev) => prev + text),
        onError: (msg) => setError(msg),
        onDone: (data) => {
          setCost(data.cost_cents);
          void load();
        },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setStreaming(false);
    }
  }

  async function exportAs(format: string) {
    if (!output) return;
    setExporting(format);
    try {
      await downloadFile(
        `/tools/export`,
        { format, markdown: output, title: `${TOOL_LABELS[tool]} - ${topic}` },
        `${TOOL_LABELS[tool]}.${format}`,
      );
      setNotice(`Exported as ${format.toUpperCase()}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(null);
    }
  }

  const tools: ToolType[] = ["lesson-plan", "assessment", "worksheet", "slides"];

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
      <Sidebar me={me} credits={credits} />

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-8">
          <div className="mb-6">
            <h1 className="text-xl font-semibold text-[#1f2937]">Teacher Tools</h1>
            <p className="text-sm text-[#6b7280] mt-1">Generate structured educational content with AI</p>
          </div>

          {error && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
          {notice && (
            <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400">
              {notice}
            </div>
          )}

          <div className="flex flex-col lg:flex-row gap-6">
            {/* Left column: Form */}
            <div className="w-full lg:w-[360px] shrink-0 space-y-4">
              <Card className="border-emerald-100 bg-white rounded-xl">
                <CardContent className="p-4 space-y-4">
                  {/* Tool tabs */}
                  <div className="flex flex-wrap gap-1.5">
                    {tools.map((t) => (
                      <button
                        key={t}
                        onClick={() => { setTool(t); setOutput(""); }}
                        className={cn(
                          "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                          tool === t
                            ? "bg-emerald-600 text-white"
                            : "bg-white text-[#6b7280] border border-emerald-100 hover:bg-emerald-50 hover:text-emerald-700"
                        )}
                      >
                        {TOOL_LABELS[t]}
                      </button>
                    ))}
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#6b7280]">Topic</label>
                    <Input placeholder="e.g., Photosynthesis, Fractions…" value={topic} onChange={(e) => setTopic(e.target.value)} />
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#6b7280]">Grade level</label>
                    <Input placeholder="e.g., Grade 5, Year 8…" value={grade} onChange={(e) => setGrade(e.target.value)} />
                  </div>

                  {tool === "lesson-plan" && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#6b7280]">Duration (minutes)</label>
                      <Input type="number" min={5} max={480} value={duration} onChange={(e) => setDuration(e.target.value)} />
                    </div>
                  )}

                  {tool === "lesson-plan" && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#6b7280]">Standards (optional)</label>
                      <Input placeholder="e.g., NGSS LS1-6" value={standards} onChange={(e) => setStandards(e.target.value)} />
                    </div>
                  )}

                  {tool === "assessment" && (
                    <>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#6b7280]">Type</label>
                        <select value={assessmentType} onChange={(e) => setAssessmentType(e.target.value)} className="h-9 w-full rounded-lg border border-emerald-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500">
                          {ASSESSMENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#6b7280]">Questions</label>
                        <Input type="number" min={1} max={100} value={questionCount} onChange={(e) => setQuestionCount(e.target.value)} />
                      </div>
                    </>
                  )}

                  {tool === "worksheet" && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#6b7280]">Type</label>
                      <select value={worksheetType} onChange={(e) => setWorksheetType(e.target.value)} className="h-9 w-full rounded-lg border border-emerald-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500">
                        {WORKSHEET_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                  )}

                  {tool === "slides" && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#6b7280]">Slide count</label>
                      <Input type="number" min={3} max={50} value={slideCount} onChange={(e) => setSlideCount(e.target.value)} />
                    </div>
                  )}

                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#6b7280]">Additional notes</label>
                    <Textarea rows={3} placeholder="Any special requirements…" value={notes} onChange={(e) => setNotes(e.target.value)} />
                  </div>

                  <Button onClick={generate} disabled={streaming || !topic.trim() || !grade.trim()} className="w-full bg-emerald-600 hover:bg-emerald-700 text-white">
                    {streaming ? (<><Loader2 className="h-4 w-4 animate-spin" /> Generating…</>) : (<><Sparkles className="h-4 w-4" /> Generate</>)}
                  </Button>

                  {cost !== null && (
                    <p className="text-xs text-center text-[#6b7280]">Cost: {(cost / 100).toFixed(2)} USD cents</p>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Right column: Output */}
            <div className="flex-1 min-w-0">
              <Card className="h-[calc(100vh-180px)] flex flex-col border-emerald-100 bg-white rounded-xl">
                <div className="flex items-center justify-between border-b border-emerald-100 px-4 py-2.5">
                  <span className="text-sm font-medium text-[#6b7280]">Output</span>
                  {output && !streaming && (
                    <div className="flex items-center gap-1.5">
                      {["docx", "pdf", "pptx"].map((fmt) => (
                        <button
                          key={fmt}
                          onClick={() => void exportAs(fmt)}
                          disabled={exporting !== null}
                          className="inline-flex h-7 items-center gap-1.5 rounded-md border border-emerald-200 bg-white px-2.5 text-xs font-medium text-[#6b7280] hover:bg-emerald-50 hover:text-emerald-700 transition-colors disabled:opacity-50"
                        >
                          {exporting === fmt ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                          {fmt.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div ref={outputRef} className="flex-1 overflow-y-auto p-6">
                  {output ? (
                    <Markdown>{output}</Markdown>
                  ) : (
                    <div className="flex h-full items-center justify-center text-[#6b7280] text-sm">
                      Generated content will appear here
                    </div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
