"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import AuthedImage from "@/components/AuthedImage";
import ImageCanvasViewer from "@/components/ImageCanvasViewer";
import { api, getToken, API_BASE, type Credits, type Me } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import {
  FolderOpen, Download, Trash2, Search, Grid3X3, List,
  FileText, Image as ImageIcon, Table as TableIcon, File,
  CheckSquare, Square, AlertCircle, Eye, ChevronLeft, ChevronRight,
  MessageSquare,
} from "lucide-react";

type FileItem = {
  id: string;
  filename: string;
  mime_type: string;
  kind: string;
  size_bytes: number;
  source_type: string;
  conversation_id: string | null;
  message_id: string | null;
  created_at: string;
  download_url: string;
  category: string;
  thumbnail_url?: string | null;
};

type FileListResponse = {
  items: FileItem[];
  total: number;
  page: number;
  per_page: number;
};

const KIND_ICONS: Record<string, typeof FileText> = {
  image: ImageIcon,
  pdf: FileText,
  docx: FileText,
  pptx: TableIcon,
  xlsx: TableIcon,
  csv: TableIcon,
  txt: FileText,
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function downloadFile(url: string, filename: string) {
  const token = getToken();
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

export default function FilesPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [credits, setCredits] = useState<Credits | null>(null);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ count: number; all?: boolean } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [storageUsed, setStorageUsed] = useState(0);
  const [storageQuota, setStorageQuota] = useState(0);

  // Canvas viewer state
  const [viewerImages, setViewerImages] = useState<string[]>([]);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [viewerLabels, setViewerLabels] = useState<string[]>([]);

  const perPage = viewMode === "grid" ? 24 : 50;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
      if (search) params.set("search", search);
      if (kindFilter) params.set("kind", kindFilter);
      const [user, credit, fileList, storage] = await Promise.all([
        api<Me>("/auth/me"),
        api<Credits>("/auth/me/credits"),
        api<FileListResponse>(`/files?${params.toString()}`),
        api<{ used_bytes: number; quota_bytes: number }>("/uploads/me/storage-usage"),
      ]);
      setMe(user);
      setCredits(credit);
      setFiles(fileList.items);
      setTotal(fileList.total);
      setStorageUsed(storage.used_bytes);
      setStorageQuota(storage.quota_bytes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load files");
    } finally {
      setLoading(false);
    }
  }, [page, perPage, search, kindFilter]);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    load();
  }, [router, load]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === files.length) setSelected(new Set());
    else setSelected(new Set(files.map((f) => f.id)));
  };

  const deleteSelected = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      if (confirmDelete.all) {
        await api("/files/all", { method: "DELETE" });
      } else {
        const ids = Array.from(selected);
        await api("/files/bulk-delete", {
          method: "POST",
          body: JSON.stringify(ids),
        });
      }
      setSelected(new Set());
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete files");
    } finally {
      setDeleting(false);
    }
  };

  const openViewer = async (file: FileItem) => {
    if (file.kind !== "image") return;
    const token = getToken();
    const path = file.download_url.startsWith("/api/")
      ? file.download_url
      : `${API_BASE}${file.download_url}`;
    // Collect all image URLs for navigation
    const imageFiles = files.filter((f) => f.kind === "image");
    const urls: string[] = [];
    const labels: string[] = [];
    let idx = 0;
    for (let i = 0; i < imageFiles.length; i++) {
      const f = imageFiles[i];
      const p = f.download_url.startsWith("/api/") ? f.download_url : `${API_BASE}${f.download_url}`;
      urls.push(p);
      labels.push(f.filename || `Image ${i + 1}`);
      if (f.id === file.id) idx = i;
    }
    setViewerImages(urls);
    setViewerLabels(labels);
    setViewerIndex(idx);
  };

  const totalPages = Math.ceil(total / perPage) || 1;

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
      <Sidebar me={me} credits={credits} />

      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-emerald-100 bg-white">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600">
              <FolderOpen className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-800">My Files</h1>
              <p className="text-xs text-gray-500">{total} file{total !== 1 ? "s" : ""} · {formatBytes(storageUsed)} / {formatBytes(storageQuota)}</p>
              {storageQuota > 0 && (
                <div className="mt-1 h-1.5 w-32 rounded-full bg-emerald-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      storageUsed / storageQuota > 0.9 ? "bg-red-500" : storageUsed / storageQuota > 0.7 ? "bg-yellow-500" : "bg-emerald-500"
                    }`}
                    style={{ width: `${Math.min(100, (storageUsed / storageQuota) * 100)}%` }}
                  />
                </div>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Search */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
              <input
                type="text"
                placeholder="Search files..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                className="h-8 w-48 rounded-md border border-emerald-200 bg-white pl-8 pr-3 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </div>

            {/* Kind filter */}
            <select
              value={kindFilter}
              onChange={(e) => { setKindFilter(e.target.value); setPage(1); }}
              className="h-8 rounded-md border border-emerald-200 bg-white px-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="">All types</option>
              <option value="image">Images</option>
              <option value="pdf">PDFs</option>
              <option value="docx">Word</option>
              <option value="pptx">PowerPoint</option>
              <option value="xlsx">Excel</option>
              <option value="csv">CSV</option>
            </select>

            {/* View toggle */}
            <button
              onClick={() => setViewMode(viewMode === "grid" ? "list" : "grid")}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-emerald-200 text-gray-600 hover:bg-emerald-50"
              title={viewMode === "grid" ? "Switch to list view" : "Switch to grid view"}
            >
              {viewMode === "grid" ? <List className="h-4 w-4" /> : <Grid3X3 className="h-4 w-4" />}
            </button>

            {/* Bulk actions */}
            {selected.size > 0 && (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirmDelete({ count: selected.size })}
                className="gap-1.5 text-xs"
              >
                <Trash2 className="h-3.5 w-3.5" /> Delete ({selected.size})
              </Button>
            )}
            {files.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmDelete({ count: total, all: true })}
                className="gap-1.5 text-xs text-red-600 border-red-200 hover:bg-red-50"
              >
                <Trash2 className="h-3.5 w-3.5" /> Delete All
              </Button>
            )}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mx-6 mt-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600">×</button>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-emerald-200 border-t-emerald-600" />
            </div>
          ) : files.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-60 text-center space-y-3">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 border border-emerald-100">
                <FolderOpen className="h-7 w-7 text-emerald-400" />
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-700">No files yet</p>
                <p className="text-xs text-gray-500 mt-1">Upload or generate images in chat to see them here.</p>
              </div>
            </div>
          ) : viewMode === "grid" ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
              {files.map((f) => {
                const Icon = KIND_ICONS[f.kind] || File;
                const isSelected = selected.has(f.id);
                return (
                  <Card
                    key={f.id}
                    className={`relative rounded-xl border bg-white shadow-sm overflow-hidden cursor-pointer transition-all hover:shadow-md ${
                      isSelected ? "border-emerald-500 ring-2 ring-emerald-200" : "border-emerald-100"
                    }`}
                    onClick={() => f.kind === "image" ? openViewer(f) : undefined}
                  >
                    {/* Checkbox */}
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleSelect(f.id); }}
                      className="absolute top-2 left-2 z-10 rounded-full bg-white/80 p-1 shadow-sm hover:bg-white"
                    >
                      {isSelected
                        ? <CheckSquare className="h-4 w-4 text-emerald-600" />
                        : <Square className="h-4 w-4 text-gray-400" />
                      }
                    </button>

                    {/* Thumbnail / Icon */}
                    <div className="aspect-square flex items-center justify-center bg-gray-50 p-4">
                      {f.thumbnail_url ? (
                        // A plain <img> cannot carry Authorization, and the
                        // thumbnail route requires a bearer token, so the raw
                        // request returned 401 and onError hid it. AuthedImage
                        // fetches with the token and hands the bytes over as
                        // a blob URL instead.
                        <AuthedImage
                          src={f.thumbnail_url}
                          alt={f.filename}
                          className="h-full w-full object-cover rounded-lg"
                        />
                      ) : f.kind === "image" ? (
                        <AuthedImage
                          src={f.download_url}
                          alt={f.filename}
                          className="h-full w-full object-cover rounded-lg"
                        />
                      ) : (
                        <Icon className="h-12 w-12 text-gray-300" />
                      )}
                    </div>

                    {/* Info */}
                    <div className="p-3 space-y-1">
                      <p className="text-xs font-medium text-gray-800 truncate" title={f.filename}>
                        {f.filename || "Untitled"}
                      </p>
                      <div className="flex items-center justify-between text-[10px] text-gray-500">
                        <span>{formatBytes(f.size_bytes)}</span>
                        <span>{f.source_type}</span>
                      </div>
                    </div>

                    {/* Actions overlay */}
                    <div className="absolute bottom-0 right-0 p-2 flex gap-1 opacity-0 group-hover:opacity-100 hover:opacity-100">
                      {f.conversation_id && (
                        <button
                          onClick={(e) => { e.stopPropagation(); router.push(`/chat?c=${f.conversation_id}`); }}
                          className="flex h-7 w-7 items-center justify-center rounded-md bg-white/90 text-gray-600 shadow-sm hover:bg-emerald-50 hover:text-emerald-700"
                          title="Open in conversation"
                        >
                          <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); void downloadFile(f.download_url, f.filename); }}
                        className="flex h-7 w-7 items-center justify-center rounded-md bg-white/90 text-gray-600 shadow-sm hover:bg-emerald-50 hover:text-emerald-700"
                        title="Download"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </Card>
                );
              })}
            </div>
          ) : (
            /* List view */
            <div className="space-y-1">
              {/* Header row */}
              <div className="flex items-center gap-3 px-4 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-emerald-100">
                <button onClick={toggleAll} className="shrink-0">
                  {selected.size === files.length && files.length > 0
                    ? <CheckSquare className="h-4 w-4 text-emerald-600" />
                    : <Square className="h-4 w-4 text-gray-400" />
                  }
                </button>
                <span className="flex-1">Name</span>
                <span className="w-20 text-right">Size</span>
                <span className="w-20">Type</span>
                <span className="w-24">Source</span>
                <span className="w-32">Date</span>
                <span className="w-20 text-right">Actions</span>
              </div>
              {files.map((f) => {
                const Icon = KIND_ICONS[f.kind] || File;
                const isSelected = selected.has(f.id);
                return (
                  <div
                    key={f.id}
                    className={`flex items-center gap-3 px-4 py-2.5 rounded-lg transition-colors cursor-pointer ${
                      isSelected ? "bg-emerald-50 border border-emerald-200" : "hover:bg-gray-50 border border-transparent"
                    }`}
                    onClick={() => f.kind === "image" ? openViewer(f) : undefined}
                  >
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleSelect(f.id); }}
                      className="shrink-0"
                    >
                      {isSelected
                        ? <CheckSquare className="h-4 w-4 text-emerald-600" />
                        : <Square className="h-4 w-4 text-gray-400" />
                      }
                    </button>
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <Icon className="h-4 w-4 shrink-0 text-gray-400" />
                      <span className="text-sm text-gray-800 truncate">{f.filename || "Untitled"}</span>
                    </div>
                    <span className="w-20 text-right text-xs text-gray-500">{formatBytes(f.size_bytes)}</span>
                    <span className="w-20 text-xs text-gray-500 uppercase">{f.kind}</span>
                    <span className="w-24 text-xs text-gray-500 capitalize">{f.source_type}</span>
                    <span className="w-32 text-xs text-gray-500">
                      {new Date(f.created_at).toLocaleDateString()}
                    </span>
                    <div className="w-28 flex justify-end gap-1">
                      {f.conversation_id && (
                        <button
                          onClick={(e) => { e.stopPropagation(); router.push(`/chat?c=${f.conversation_id}`); }}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 hover:bg-emerald-50 hover:text-emerald-600"
                          title="Open in conversation"
                        >
                          <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); void downloadFile(f.download_url, f.filename); }}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 hover:bg-emerald-50 hover:text-emerald-600"
                        title="Download"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(new Set([f.id]));
                          setConfirmDelete({ count: 1 });
                        }}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 hover:bg-red-50 hover:text-red-600"
                        title="Delete"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-8">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-emerald-200 text-gray-600 hover:bg-emerald-50 disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-sm text-gray-600">Page {page} of {totalPages}</span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-emerald-200 text-gray-600 hover:bg-emerald-50 disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </main>

      {/* Delete confirmation dialog */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="relative w-full max-w-sm rounded-xl bg-white p-6 shadow-2xl border border-red-100">
            <h3 className="text-lg font-bold text-gray-800 mb-2">
              {confirmDelete.all ? "Delete all files?" : `Delete ${confirmDelete.count} file${confirmDelete.count > 1 ? "s" : ""}?`}
            </h3>
            <p className="text-sm text-gray-600 mb-6">
              {confirmDelete.all
                ? "This action will permanently remove all files in your account and cannot be undone."
                : "These files will be permanently removed."}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmDelete(null)}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void deleteSelected()}
                disabled={deleting}
                className="gap-1.5"
              >
                {deleting ? "Deleting..." : confirmDelete.all ? "Delete All" : "Delete Files"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Canvas Viewer */}
      {viewerImages.length > 0 && (
        <ImageCanvasViewer
          images={viewerImages}
          index={viewerIndex}
          labels={viewerLabels}
          onClose={() => setViewerImages([])}
          onNavigate={setViewerIndex}
        />
      )}
    </div>
  );
}
