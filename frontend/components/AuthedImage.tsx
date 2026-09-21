"use client";

import { useEffect, useState } from "react";
import { API_BASE, getToken } from "@/lib/api";
import ImageCanvasViewer from "./ImageCanvasViewer";

export default function AuthedImage({
  src,
  alt,
  className,
}: {
  src: string;
  alt: string;
  className?: string;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);

  useEffect(() => {
    let revoked = false;
    let created: string | null = null;

    (async () => {
      try {
        const path = src.startsWith("/media/images/")
          ? `${API_BASE}/images/file/${src.split("/").pop()}`
          : src.startsWith("/api/") && API_BASE !== "/api"
            ? src.replace(/^\/api/, API_BASE)
            : src;

        const token = getToken();
        const res = await fetch(path, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(String(res.status));

        const blob = await res.blob();
        if (revoked) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      } catch {
        if (!revoked) setFailed(true);
      }
    })();

    return () => {
      revoked = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src]);

  if (failed) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground"
        role="img"
        aria-label={`${alt} (unavailable)`}
      >
        This image is no longer available.
      </div>
    );
  }

  if (!objectUrl) {
    return (
      <div
        className={className ?? "w-full max-w-[420px] aspect-square rounded-xl border border-border bg-gradient-to-r from-muted via-muted/50 to-muted bg-[length:200%_100%] animate-pulse"}
        aria-label="Loading image"
      />
    );
  }

  // eslint-disable-next-line @next/next/no-img-element
  return (
    <>
      <img
        src={objectUrl}
        alt={alt}
        className={`${className ?? ""} cursor-zoom-in`}
        onClick={() => setViewerOpen(true)}
      />
      {viewerOpen && objectUrl && (
        <ImageCanvasViewer
          images={[objectUrl]}
          index={0}
          labels={[alt]}
          onClose={() => setViewerOpen(false)}
          onNavigate={() => {}}
        />
      )}
    </>
  );
}
