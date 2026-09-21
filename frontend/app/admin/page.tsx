"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import AuthedImage from "@/components/AuthedImage";
import { api, formatCents, getToken, API_BASE, type Credits, type Me } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import {
  AlertCircle, Users, DollarSign, MessageSquare, ImageIcon,
  Check, X, Download, FolderOpen, FileText, ArrowLeft,
  TrendingUp, Activity, Shield, Eye, EyeOff, UserPlus, Clock
} from "lucide-react";

const ROLES = [
  "SUPER_ADMIN", "ADMIN", "IT_ADMIN", "PRINCIPAL",
  "REGISTRAR", "TEACHER", "STUDENT", "PARENT",
];

type UsageRow = {
  user_id: string;
  full_name: string;
  email: string;
  role: string;
  text_calls: number;
  image_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cents: number;
  allocation_cents: number;
};

type Summary = {
  cost_cents: number;
  prompt_tokens: number;
  completion_tokens: number;
  images: number;
  active_users: number;
  by_day: { day: string; cost_cents: number }[];
};

type MediaImage = {
  id: string;
  prompt: string;
  url: string;
  size: string;
  quality: string;
  cost_cents: number;
  created_at: string;
  type: "generated";
};

type MediaAttachment = {
  id: string;
  filename: string;
  kind: string;
  mime_type: string;
  size_bytes: number;
  url: string;
  created_at: string;
  type: "uploaded";
};

type UserMedia = {
  images: MediaImage[];
  attachments: MediaAttachment[];
};

async function downloadFile(url: string, filename: string) {
  const token = getToken();
  // Backend returns URLs like /api/images/file/... or /api/admin/files/...
  // which already include the API prefix, so don't prepend API_BASE again.
  const path = url.startsWith("/api/") ? url : `${API_BASE}${url}`;
  const res = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Download failed");
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// MABDC Style Colors
const MABDC_GREEN = "#10b981"; // Emerald 500-ish
const MABDC_BG = "#f0fdf4"; // Green 50
const MABDC_BORDER = "#bbf7d0"; // Green 200

function SpendChart({ data }: { data: { day: string; cost_cents: number }[] }) {
  if (data.length === 0) return <p className="text-sm text-muted-foreground">No usage recorded yet.</p>;
  const width = 900, height = 200, padX = 40, padY = 20;
  const peak = Math.max(...data.map((d) => d.cost_cents), 1);
  const plotW = width - padX * 2, plotH = height - padY * 2;
  const slot = plotW / data.length;
  const barW = Math.max(Math.min(slot * 0.6, 32), 3);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" role="img" aria-label="Daily spend chart">
      <defs>
        <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={MABDC_GREEN} stopOpacity="0.9" />
          <stop offset="100%" stopColor={MABDC_GREEN} stopOpacity="0.4" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const y = padY + plotH - f * plotH;
        return (
          <g key={f}>
            <line x1={padX} y1={y} x2={width - padX} y2={y} stroke="currentColor" className="text-border/50" strokeWidth="1" strokeDasharray={f === 0 ? "0" : "4 4"} />
            <text x={padX - 6} y={y + 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {formatCents(Math.round(peak * f))}
            </text>
          </g>
        );
      })}
      {data.map((d, i) => {
        const h = (d.cost_cents / peak) * plotH;
        const x = padX + i * slot + (slot - barW) / 2;
        const y = padY + plotH - h;
        return (
          <g key={d.day}>
            <rect x={x} y={y} width={barW} height={Math.max(h, 2)} rx="3" fill="url(#barGrad)" className="transition-all hover:opacity-80" />
            <title>{d.day}: {formatCents(d.cost_cents)}</title>
          </g>
        );
      })}
    </svg>
  );
}

const ROLE_ICONS: Record<string, typeof Shield> = {
  SUPER_ADMIN: Shield,
  ADMIN: Shield,
  IT_ADMIN: Activity,
  TEACHER: Users,
  STUDENT: Users,
};

export default function AdminPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [credits, setCredits] = useState<Credits | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<UsageRow[]>([]);
  const [users, setUsers] = useState<{ id: string; email: string; full_name: string; role: string; is_active: boolean; monthly_allocation_cents: number; used_cents: number; status: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editingCredit, setEditingCredit] = useState<string | null>(null);
  const [creditValue, setCreditValue] = useState("");
  const [showCosts, setShowCosts] = useState(true);

  // Create user state
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [createForm, setCreateForm] = useState({ full_name: "", email: "", password: "", role: "TEACHER", monthly_allocation_cents: "500" });
  const [creating, setCreating] = useState(false);

  // Media viewer state
  const [mediaUser, setMediaUser] = useState<{ id: string; name: string } | null>(null);
  const [media, setMedia] = useState<UserMedia | null>(null);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [lightboxImg, setLightboxImg] = useState<MediaImage | null>(null);
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(async () => {
    const [user, credit, sum, usage, userList] = await Promise.all([
      api<Me>("/auth/me"),
      api<Credits>("/auth/me/credits"),
      api<Summary>("/admin/usage/summary"),
      api<UsageRow[]>("/admin/usage"),
      api<typeof users>("/admin/users"),
    ]);
    setMe(user);
    setCredits(credit);
    setSummary(sum);
    setRows(usage);
    setUsers(userList);
  }, []);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    load().catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [router, load]);

  useEffect(() => {
    if (!lightboxImg) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightboxImg(null);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [lightboxImg]);

  async function saveCredit(userId: string) {
    const cents = parseInt(creditValue, 10);
    if (isNaN(cents) || cents < 0) return;
    try {
      await api(`/admin/users/${userId}/credits`, {
        method: "PUT",
        body: JSON.stringify({ monthly_allocation_cents: cents }),
      });
      setEditingCredit(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update credits");
    }
  }

  async function createUser() {
    if (!createForm.full_name || !createForm.email || !createForm.password) return;
    if (createForm.password.length < 10) {
      setError("Password must be at least 10 characters");
      return;
    }
    setCreating(true);
    try {
      await api("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          full_name: createForm.full_name,
          email: createForm.email,
          password: createForm.password,
          role: createForm.role,
          monthly_allocation_cents: parseInt(createForm.monthly_allocation_cents, 10) || 0,
        }),
      });
      setShowCreateUser(false);
      setCreateForm({ full_name: "", email: "", password: "", role: "TEACHER", monthly_allocation_cents: "500" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  async function approveUser(userId: string) {
    try {
      await api(`/admin/users/${userId}/approve`, { method: "PATCH" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve user");
    }
  }

  async function toggleActive(userId: string, active: boolean) {
    try {
      await api(`/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify({ is_active: !active }) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    }
  }

  async function openMedia(userId: string, userName: string) {
    setMediaUser({ id: userId, name: userName });
    setMediaLoading(true);
    setMedia(null);
    try {
      const data = await api<UserMedia>(`/admin/users/${userId}/media`);
      setMedia(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load media");
    } finally {
      setMediaLoading(false);
    }
  }

  function closeMedia() {
    setMediaUser(null);
    setMedia(null);
    setLightboxImg(null);
  }

  async function handleDownloadImage(img: MediaImage, e?: React.MouseEvent) {
    if (e) e.stopPropagation();
    setDownloading(true);
    try {
      const filename = `${img.prompt.slice(0, 30).replace(/[^a-z0-9]/gi, "_")}_${img.size}.png`;
      await downloadFile(img.url, filename);
    } catch {
      setError("Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function handleDownloadAttachment(att: MediaAttachment, e?: React.MouseEvent) {
    if (e) e.stopPropagation();
    setDownloading(true);
    try {
      await downloadFile(att.url, att.filename);
    } catch {
      setError("Download failed");
    } finally {
      setDownloading(false);
    }
  }

  const stats = [
    { label: "Total Spend", value: summary ? formatCents(summary.cost_cents) : "—", icon: DollarSign, color: "text-emerald-600", bg: "bg-emerald-50" },
    { label: "Active Users", value: summary?.active_users ?? "—", icon: Users, color: "text-emerald-600", bg: "bg-emerald-50" },
    { label: "Text Calls", value: rows.reduce((a, r) => a + r.text_calls, 0).toLocaleString(), icon: MessageSquare, color: "text-emerald-600", bg: "bg-emerald-50" },
    { label: "Images Generated", value: summary?.images?.toLocaleString() ?? "—", icon: ImageIcon, color: "text-emerald-600", bg: "bg-emerald-50" },
  ];

  // ── Media Viewer ──────────────────────────────────────────────────────
  if (mediaUser) {
    return (
      <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
        <Sidebar me={me} credits={credits} />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-6 py-8">
            <div className="mb-8 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <Button variant="outline" size="sm" onClick={closeMedia} className="gap-2 rounded-md border-emerald-200 text-emerald-700 hover:bg-emerald-50">
                  <ArrowLeft className="h-4 w-4" /> Back to Dashboard
                </Button>
                <div>
                  <h1 className="text-xl font-bold text-gray-800">{mediaUser.name}&apos;s Media</h1>
                  <p className="text-sm text-gray-500 mt-1">
                    {media ? `${media.images.length} generated · ${media.attachments.length} uploaded` : "Loading..."}
                  </p>
                </div>
              </div>
            </div>

            {mediaLoading && (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className="aspect-square rounded-xl bg-emerald-50 animate-pulse" />
                ))}
              </div>
            )}

            {!mediaLoading && media && (
              <div className="space-y-10">
                {media.images.length > 0 && (
                  <section>
                    <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4 flex items-center gap-2">
                      <ImageIcon className="h-4 w-4 text-emerald-600" /> Generated Images
                    </h2>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                      {media.images.map((img) => (
                        <Card
                          key={img.id}
                          className="group overflow-hidden cursor-pointer hover:shadow-md transition-all duration-200 rounded-xl border-emerald-100 bg-white"
                          onClick={() => setLightboxImg(img)}
                        >
                          <div className="aspect-square relative overflow-hidden">
                            <AuthedImage src={img.url} alt={img.prompt} className="w-full h-full object-cover" />
                            <button
                              onClick={(e) => handleDownloadImage(img, e)}
                              disabled={downloading}
                              className="absolute top-2 right-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-black/50 backdrop-blur-sm text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/70 disabled:opacity-50"
                              aria-label="Download image"
                            >
                              <Download className="h-4 w-4" />
                            </button>
                            <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-3">
                              <p className="text-xs text-white line-clamp-2 mb-1.5 font-medium">{img.prompt}</p>
                              <div className="flex items-center gap-1.5">
                                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-white/20 text-white border-0">{img.size}</Badge>
                                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-white/20 text-white border-0 capitalize">{img.quality}</Badge>
                              </div>
                            </div>
                          </div>
                        </Card>
                      ))}
                    </div>
                  </section>
                )}

                {media.attachments.length > 0 && (
                  <section>
                    <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4 flex items-center gap-2">
                      <FolderOpen className="h-4 w-4 text-emerald-600" /> Uploaded Files
                    </h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                      {media.attachments.map((att) => (
                        <Card key={att.id} className="group flex items-center gap-4 p-4 hover:shadow-md transition-all rounded-xl border-emerald-100 bg-white">
                          <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ${
                            att.kind === "image" ? "bg-emerald-50 text-emerald-600" :
                            att.kind === "pdf" ? "bg-red-50 text-red-600" :
                            "bg-gray-50 text-gray-600"
                          }`}>
                            {att.kind === "image" ? <ImageIcon className="h-5 w-5" /> : <FileText className="h-5 w-5" />}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-800 truncate">{att.filename}</p>
                            <p className="text-xs text-gray-500 mt-0.5">
                              {att.kind.toUpperCase()} · {formatBytes(att.size_bytes)} · {new Date(att.created_at).toLocaleDateString()}
                            </p>
                          </div>
                          <button
                            onClick={(e) => handleDownloadAttachment(att, e)}
                            disabled={downloading}
                            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-400 hover:bg-emerald-50 hover:text-emerald-600 transition-colors disabled:opacity-50"
                            aria-label={`Download ${att.filename}`}
                          >
                            <Download className="h-4 w-4" />
                          </button>
                        </Card>
                      ))}
                    </div>
                  </section>
                )}

                {media.images.length === 0 && media.attachments.length === 0 && (
                  <Card className="flex flex-col items-center justify-center py-20 text-center rounded-xl border-emerald-100 bg-white">
                    <FolderOpen className="h-12 w-12 text-gray-300 mb-4" />
                    <p className="text-gray-500">No media files for this user yet.</p>
                  </Card>
                )}
              </div>
            )}
          </div>
        </main>

        {/* Lightbox */}
        {lightboxImg && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
            onClick={() => setLightboxImg(null)}
          >
            <div
              className="relative flex max-h-[92vh] max-w-[92vw] flex-col items-center rounded-2xl bg-white p-5 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => setLightboxImg(null)}
                className="absolute -top-3 -right-3 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-white shadow-lg border border-gray-200 text-gray-500 hover:text-gray-800 transition-colors"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
              <div className="max-h-[72vh] max-w-full overflow-hidden rounded-xl">
                <AuthedImage src={lightboxImg.url} alt={lightboxImg.prompt} className="max-h-[72vh] max-w-full object-contain" />
              </div>
              <div className="mt-5 w-full max-w-lg space-y-3 text-center">
                <p className="text-sm text-gray-700 leading-relaxed">{lightboxImg.prompt}</p>
                <div className="flex items-center justify-center gap-2">
                  <Badge variant="secondary" className="bg-emerald-50 text-emerald-700 border-emerald-200">{lightboxImg.size}</Badge>
                  <Badge variant="secondary" className="bg-emerald-50 text-emerald-700 border-emerald-200 capitalize">{lightboxImg.quality}</Badge>
                </div>
                <Button 
                  variant="default" 
                  size="sm" 
                  onClick={() => handleDownloadImage(lightboxImg)} 
                  disabled={downloading} 
                  className="gap-2 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white mt-2"
                >
                  <Download className="h-4 w-4" />
                  {downloading ? "Downloading..." : "Download"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Main Dashboard ────────────────────────────────────────────────────
  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
      <Sidebar me={me} credits={credits} />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-6 py-8 space-y-8">
          
          {/* Header */}
          <div className="flex items-center justify-between border-b border-emerald-100 pb-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-600 text-white">
                <Shield className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-800"><span className="text-emerald-600">MABDC</span> Admin</h1>
                <p className="text-xs text-gray-500 mt-0.5">User Management & Platform Settings</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Button 
                variant="default" 
                size="sm" 
                onClick={() => setShowCreateUser(true)}
                className="rounded-md bg-emerald-600 hover:bg-emerald-700 text-white gap-2 shadow-sm"
              >
                <UserPlus className="h-4 w-4" /> Create User
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowCosts(!showCosts)}
                className="rounded-md text-gray-500 hover:text-gray-800 hover:bg-gray-100 gap-2"
                title={showCosts ? "Hide credit amounts" : "Show credit amounts"}
              >
                {showCosts ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>



          {error && (
            <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {/* Stats Grid - MABDC Style */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {stats.map(({ label, value, icon: Icon, color, bg }) => (
              <Card key={label} className="rounded-xl border-emerald-100 bg-white shadow-sm hover:shadow-md transition-shadow">
                <CardContent className="p-5 flex items-center gap-4">
                  <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-lg ${bg}`}>
                    <Icon className={`h-6 w-6 ${color}`} />
                  </div>
                  <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</p>
                    <p className="text-2xl font-bold text-gray-800 mt-0.5">
                      {showCosts || label !== "Total Spend" ? value : "••••"}
                    </p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Spend Chart */}
          {summary?.by_day && summary.by_day.length > 0 && (
            <Card className="rounded-xl border-emerald-100 bg-white shadow-sm">
              <CardHeader className="pb-2 border-b border-gray-100">
                <CardTitle className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-emerald-600" />
                  Daily Spend (last 30 days)
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-4">
                {showCosts ? <SpendChart data={summary.by_day} /> : (
                  <div className="flex items-center justify-center h-[200px] text-gray-400 text-sm">
                    Click Eye Icon to reveal the spend chart
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Pending Approvals Section */}
          {users.filter((u) => u.status === "pending").length > 0 && (
            <Card className="rounded-xl border-amber-200 bg-amber-50/50 shadow-sm">
              <CardHeader className="pb-3 border-b border-amber-200/60">
                <CardTitle className="text-sm font-semibold text-amber-800 flex items-center gap-2">
                  <Clock className="h-4 w-4 text-amber-500" />
                  Pending Approvals
                  <span className="ml-auto inline-flex items-center justify-center rounded-full bg-amber-200 px-2.5 py-0.5 text-xs font-bold text-amber-800">
                    {users.filter((u) => u.status === "pending").length}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 divide-y divide-amber-200/60">
                {users
                  .filter((u) => u.status === "pending")
                  .map((u) => (
                    <div key={u.id} className="flex items-center justify-between px-5 py-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-600">
                          <Users className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-gray-800 truncate">{u.full_name}</p>
                          <p className="text-xs text-gray-500 truncate">{u.email}</p>
                        </div>
                        <span className="hidden sm:inline-block text-xs font-medium text-blue-700 bg-blue-50 px-2 py-0.5 rounded border border-blue-200 shrink-0">
                          {u.role.replace(/_/g, " ")}
                        </span>
                      </div>
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => approveUser(u.id)}
                        className="shrink-0 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white gap-1.5 text-xs"
                      >
                        <Check className="h-3.5 w-3.5" /> Approve
                      </Button>
                    </div>
                  ))}
              </CardContent>
            </Card>
          )}

          {/* Users List - MABDC Card Style */}
          <div className="space-y-4">
            {users.map((u) => {
              const RoleIcon = ROLE_ICONS[u.role] || Users;
              return (
                <Card key={u.id} className="rounded-xl border-emerald-100 bg-white shadow-sm overflow-hidden">
                  <div className="p-5">
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                      {/* Left: Info */}
                      <div className="flex items-start gap-4">
                        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100">
                          <RoleIcon className="h-6 w-6" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="text-lg font-bold text-gray-800">{u.full_name}</h3>
                            {u.status === "pending" ? (
                              <span className="flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                                <Clock className="h-3 w-3" /> Pending
                              </span>
                            ) : u.status === "disabled" ? (
                              <span className="flex items-center gap-1 text-xs font-medium text-red-700 bg-red-50 px-2 py-0.5 rounded border border-red-200">
                                Disabled
                              </span>
                            ) : u.is_active ? (
                              <span className="flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                                <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Active
                              </span>
                            ) : (
                              <span className="flex items-center gap-1 text-xs font-medium text-gray-500 bg-gray-50 px-2 py-0.5 rounded border border-gray-200">
                                Inactive
                              </span>
                            )}
                            <span className="text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                              {u.role.replace(/_/g, " ")}
                            </span>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500">
                            <span>Email: <span className="text-gray-700 font-medium">{u.email}</span></span>
                            <span>Allocated: <span className="text-gray-700 font-medium">{showCosts ? formatCents(u.monthly_allocation_cents ?? 0) : "••••"}</span></span>
                            <span>Used: <span className="text-red-600 font-medium">{showCosts ? formatCents(u.used_cents ?? 0) : "••••"}</span></span>
                            <span>Remaining: <span className="text-emerald-600 font-medium">{showCosts ? formatCents((u.monthly_allocation_cents ?? 0) - (u.used_cents ?? 0)) : "••••"}</span></span>
                          </div>
                        </div>
                      </div>

                      {/* Right: Actions */}
                      <div className="flex items-center gap-2 md:border-l md:border-gray-100 md:pl-4">
                        <Button 
                          variant="outline" 
                          size="sm" 
                          onClick={() => openMedia(u.id, u.full_name)}
                          className="rounded-md border-gray-200 text-gray-600 hover:bg-gray-50 gap-2"
                        >
                          <FolderOpen className="h-4 w-4" /> Media
                        </Button>
                        <div
                          className={`relative inline-flex h-6 w-11 cursor-pointer items-center rounded-full transition-colors ${u.is_active ? 'bg-emerald-500' : 'bg-gray-300'}`}
                          onClick={() => toggleActive(u.id, u.is_active)}
                          title={u.is_active ? "Disable user" : "Enable user"}
                        >
                          <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${u.is_active ? 'translate-x-6' : 'translate-x-1'}`} />
                        </div>
                      </div>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      </main>

      {/* Create User Modal */}
      {showCreateUser && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={() => setShowCreateUser(false)}
        >
          <div
            className="relative w-full max-w-md rounded-xl bg-white p-6 shadow-2xl border border-emerald-100"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setShowCreateUser(false)}
              className="absolute top-4 right-4 flex h-8 w-8 items-center justify-center rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="mb-6">
              <h2 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                <div className="h-8 w-8 rounded-lg bg-emerald-100 flex items-center justify-center text-emerald-600">
                  <UserPlus className="h-4 w-4" />
                </div>
                Create User
              </h2>
              <p className="text-sm text-gray-500 mt-1">Add a new account to the platform</p>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">Full Name</label>
                <Input
                  placeholder="Juan Dela Cruz"
                  value={createForm.full_name}
                  onChange={(e) => setCreateForm({ ...createForm, full_name: e.target.value })}
                  className="rounded-md border-gray-300 focus:border-emerald-500 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">Email</label>
                <Input
                  type="email"
                  placeholder="juan@mabdc.org"
                  value={createForm.email}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                  className="rounded-md border-gray-300 focus:border-emerald-500 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">Password</label>
                <Input
                  type="password"
                  placeholder="Minimum 10 characters"
                  value={createForm.password}
                  onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                  className="rounded-md border-gray-300 focus:border-emerald-500 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">Role</label>
                <select
                  value={createForm.role}
                  onChange={(e) => setCreateForm({ ...createForm, role: e.target.value })}
                  className="flex h-9 w-full rounded-md border border-gray-300 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>{r.replace(/_/g, " ")}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">Monthly Credits (cents)</label>
                <Input
                  type="number"
                  value={createForm.monthly_allocation_cents}
                  onChange={(e) => setCreateForm({ ...createForm, monthly_allocation_cents: e.target.value })}
                  className="rounded-md border-gray-300 focus:border-emerald-500 focus:ring-emerald-500"
                />
                <p className="text-xs text-gray-400 mt-1">500 = $5.00/month</p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowCreateUser(false)}
                className="rounded-md border-gray-300 text-gray-600 hover:bg-gray-50"
              >
                Cancel
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={createUser}
                disabled={creating || !createForm.full_name || !createForm.email || !createForm.password}
                className="rounded-md bg-emerald-600 hover:bg-emerald-700 text-white gap-2"
              >
                {creating ? (
                  <><Activity className="h-4 w-4 animate-spin" /> Creating...</>
                ) : (
                  <><UserPlus className="h-4 w-4" /> Create User</>
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
