"use client";

import { upload } from "@vercel/blob/client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { SourceRecord } from "@/lib/github";
import { assertSourceFilename, assertSourceSize, SOURCE_MAX_BYTES, SourceValidationError } from "@/lib/sources";

type UploadState = "idle" | "uploading" | "queued" | "error";
type Receiver = { id: string; label: string; host: string; online: boolean; lastSeenAt: string | null; commandRevision: string | null; pollIntervalMs: number };
type FolderItem = { path: string; name: string; sha: string | null };
type DashboardTab = "library" | "picture-in-picture";
type PictureInPictureLayout = { x: number; y: number; width: number; height: number };
type PictureInPictureDrag = { pointerId: number; mode: "move" | "resize"; originX: number; originY: number; layout: PictureInPictureLayout };
type PictureInPicturePreviewSource = Pick<SourceRecord, "name" | "downloadUrl">;
type PendingPictureInPictureUpload = { path: string; slot: "base" | "overlay"; previewUrl: string; name: string };

const DEFAULT_PICTURE_IN_PICTURE_LAYOUT: PictureInPictureLayout = { x: 0.64, y: 0.06, width: 0.3, height: 0.3 };
const PICTURE_IN_PICTURE_SELECTION = "picture-in-picture";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function sourceIsPictureInPictureReady(source: SourceRecord): boolean {
  const extension = source.name.split(".").at(-1)?.toLowerCase() || "";
  return ["mp4", "mov", "m4v", "webm", "jpg", "jpeg", "png", "gif", "bmp", "webp"].includes(extension);
}

function sourcePreviewKind(source: Pick<SourceRecord, "name">): "image" | "video" {
  return ["jpg", "jpeg", "png", "gif", "bmp", "webp"].includes(source.name.split(".").at(-1)?.toLowerCase() || "") ? "image" : "video";
}

function sourceIsImageFile(file: File): boolean {
  const extension = file.name.split(".").at(-1)?.toLowerCase() || "";
  return ["jpg", "jpeg", "png", "gif", "bmp", "webp"].includes(extension);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let index = -1;
  let size = value;
  do { size /= 1024; index += 1; } while (size >= 1024 && index < units.length - 1);
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

function parentPath(path: string): string {
  return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}

function leafName(path: string): string {
  return path.split("/").at(-1) || path;
}

function childPath(folder: string, filename: string): string {
  return folder ? `${folder}/${filename}` : filename;
}

function relativeTime(value: string | null, now: number): string {
  if (!value) return "Last connected: never";
  const seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000));
  if (seconds < 60) return "Last connected: now";
  if (seconds < 3600) return `Last connected: ${Math.floor(seconds / 60)}m ago`;
  return `Last connected: ${Math.floor(seconds / 3600)}h ago`;
}

function nextExpectedCheck(value: string | null, intervalMs: number, now: number): string {
  const intervalLabel = `Checks every ${Math.round(intervalMs / 1000)}s`;
  const lastSeen = value ? Date.parse(value) : Number.NaN;
  if (!Number.isFinite(lastSeen) || now - lastSeen > 10 * 60 * 1000) {
    return `${intervalLabel} · waiting for receiver`;
  }
  const remainingMs = intervalMs - ((now - lastSeen) % intervalMs);
  return `${intervalLabel} · next check: about ${Math.max(1, Math.ceil(remainingMs / 1000))}s`;
}

async function apiSources(): Promise<SourceRecord[]> {
  const response = await fetch("/api/sources", { cache: "no-store" });
  const payload = await response.json() as { sources?: SourceRecord[]; error?: string };
  if (!response.ok || !payload.sources) throw new Error(payload.error || "Could not refresh sources.");
  return payload.sources;
}

async function apiReceivers(): Promise<Receiver[]> {
  const response = await fetch("/api/receivers", { cache: "no-store" });
  const payload = await response.json() as { receivers?: Receiver[]; error?: string };
  if (!response.ok || !payload.receivers) throw new Error(payload.error || "Could not load receiver status.");
  return payload.receivers;
}

function PictureInPicturePreview({
  base,
  overlay,
  layout,
  previewRef,
  onOverlayPointerDown,
  onSourceDragOver,
  onSourceDrop,
}: {
  base: PictureInPicturePreviewSource | undefined;
  overlay: PictureInPicturePreviewSource | undefined;
  layout: PictureInPictureLayout;
  previewRef: React.RefObject<HTMLDivElement | null>;
  onOverlayPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onSourceDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  onSourceDrop: (event: React.DragEvent<HTMLDivElement>) => void;
}) {
  return (
    <div className="pip-preview" ref={previewRef} aria-label="Picture-in-picture preview" onDragOver={onSourceDragOver} onDrop={onSourceDrop}>
      <div className="pip-preview-label">TV preview</div>
      {base?.downloadUrl ? sourcePreviewKind(base) === "image"
        ? <img className="pip-base" src={base.downloadUrl} alt={`Base: ${base.name}`} />
        : <video className="pip-base" src={base.downloadUrl} muted playsInline preload="metadata" aria-label={`Base video: ${base.name}`} />
        : <div className="pip-empty">Drop an image from your desktop, or choose a base source</div>}
      {overlay?.downloadUrl ? (
        <div
          className="pip-overlay-frame"
          style={{ left: `${layout.x * 100}%`, top: `${layout.y * 100}%`, width: `${layout.width * 100}%`, height: `${layout.height * 100}%` }}
          onPointerDown={onOverlayPointerDown}
          role="presentation"
        >
          {sourcePreviewKind(overlay) === "image"
            ? <img src={overlay.downloadUrl} alt={`Picture in picture: ${overlay.name}`} />
            : <video src={overlay.downloadUrl} muted playsInline preload="metadata" aria-label={`Picture in picture video: ${overlay.name}`} />}
          <span className="pip-overlay-label">Picture in picture · drag to move</span>
          <span className="pip-resize-handle" aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}

export function SourceDashboard({ initialSources }: { initialSources: SourceRecord[] }) {
  const [sources, setSources] = useState(initialSources);
  const [activeTab, setActiveTab] = useState<DashboardTab>("library");
  const [folder, setFolder] = useState("");
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [receivers, setReceivers] = useState<Receiver[]>([]);
  const [staging, setStaging] = useState<string | null>(null);
  const [selectedReceiverIds, setSelectedReceiverIds] = useState<Record<string, string[]>>({});
  const [baseSourcePath, setBaseSourcePath] = useState("");
  const [overlaySourcePath, setOverlaySourcePath] = useState("");
  const [pictureInPictureLayout, setPictureInPictureLayout] = useState<PictureInPictureLayout>(DEFAULT_PICTURE_IN_PICTURE_LAYOUT);
  const [pictureInPictureDrag, setPictureInPictureDrag] = useState<PictureInPictureDrag | null>(null);
  const pictureInPicturePreviewRef = useRef<HTMLDivElement>(null);
  const [pendingPictureInPictureUploads, setPendingPictureInPictureUploads] = useState<PendingPictureInPictureUpload[]>([]);
  const pictureInPicturePreviewUrlsRef = useRef(new Set<string>());
  const pictureInPictureUploadTimersRef = useRef(new Set<number>());
  const [now, setNow] = useState(() => Date.now());

  const files = useMemo(() => sources.filter((item) => item.kind === "file"), [sources]);
  const totalSize = useMemo(() => files.reduce((total, item) => total + item.size, 0), [files]);
  const refreshSources = useCallback(async () => {
    const updated = await apiSources();
    setSources(updated);
    return updated;
  }, []);
  const refreshReceivers = useCallback(() => apiReceivers().then(setReceivers).catch(() => undefined), []);

  const folders = useMemo(() => {
    const known = new Map<string, FolderItem>();
    for (const source of sources) {
      if (source.kind === "folder") known.set(source.path, { path: source.path, name: source.name, sha: source.sha });
      const path = source.kind === "folder" ? source.path : parentPath(source.path);
      const parts = path ? path.split("/") : [];
      for (let index = 1; index <= parts.length; index += 1) {
        const current = parts.slice(0, index).join("/");
        if (!known.has(current)) known.set(current, { path: current, name: leafName(current), sha: null });
      }
    }
    return [...known.values()].sort((left, right) => left.path.localeCompare(right.path));
  }, [sources]);

  const visibleFolders = useMemo(
    () => folders.filter((item) => parentPath(item.path) === folder),
    [folder, folders],
  );
  const visibleFiles = useMemo(
    () => files.filter((item) => parentPath(item.path) === folder).sort((left, right) => left.name.localeCompare(right.name)),
    [files, folder],
  );
  const pictureInPictureFiles = useMemo(
    () => files.filter(sourceIsPictureInPictureReady).sort((left, right) => left.path.localeCompare(right.path)),
    [files],
  );
  const baseSource = useMemo(() => pictureInPictureFiles.find((item) => item.path === baseSourcePath), [baseSourcePath, pictureInPictureFiles]);
  const overlaySource = useMemo(() => pictureInPictureFiles.find((item) => item.path === overlaySourcePath), [overlaySourcePath, pictureInPictureFiles]);
  const pendingBaseSource = pendingPictureInPictureUploads.find((item) => item.slot === "base");
  const pendingOverlaySource = pendingPictureInPictureUploads.find((item) => item.slot === "overlay");
  const basePreviewSource: PictureInPicturePreviewSource | undefined = baseSource || (pendingBaseSource ? { name: pendingBaseSource.name, downloadUrl: pendingBaseSource.previewUrl } : undefined);
  const overlayPreviewSource: PictureInPicturePreviewSource | undefined = overlaySource || (pendingOverlaySource ? { name: pendingOverlaySource.name, downloadUrl: pendingOverlaySource.previewUrl } : undefined);

  useEffect(() => { void refreshReceivers(); const timer = window.setInterval(() => void refreshReceivers(), 30_000); return () => window.clearInterval(timer); }, [refreshReceivers]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  useEffect(() => () => {
    for (const timer of pictureInPictureUploadTimersRef.current) window.clearInterval(timer);
    for (const url of pictureInPicturePreviewUrlsRef.current) URL.revokeObjectURL(url);
  }, []);
  useEffect(() => {
    if (pictureInPictureDrag === null) return;
    const drag: PictureInPictureDrag = pictureInPictureDrag;
    function move(event: PointerEvent) {
      if (event.pointerId !== drag.pointerId) return;
      const preview = pictureInPicturePreviewRef.current;
      if (!preview) return;
      const bounds = preview.getBoundingClientRect();
      const deltaX = (event.clientX - drag.originX) / bounds.width;
      const deltaY = (event.clientY - drag.originY) / bounds.height;
      setPictureInPictureLayout(() => {
        if (drag.mode === "resize") {
          const aspectRatio = Math.max(0.1, drag.layout.width / drag.layout.height);
          const widthDelta = Math.abs(deltaX) >= Math.abs(deltaY * aspectRatio) ? deltaX : deltaY * aspectRatio;
          const minimumWidth = Math.max(0.12, 0.12 * aspectRatio);
          const maximumWidth = Math.min(0.88, 1 - drag.layout.x, (1 - drag.layout.y) * aspectRatio);
          const width = clamp(drag.layout.width + widthDelta, minimumWidth, maximumWidth);
          const height = width / aspectRatio;
          return { ...drag.layout, width, height };
        }
        return {
          ...drag.layout,
          x: clamp(drag.layout.x + deltaX, 0, 1 - drag.layout.width),
          y: clamp(drag.layout.y + deltaY, 0, 1 - drag.layout.height),
        };
      });
    }
    function end(event: PointerEvent) {
      if (event.pointerId === drag.pointerId) setPictureInPictureDrag(null);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
  }, [pictureInPictureDrag]);
  useEffect(() => {
    if (!pendingPath) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      refreshSources().then((updated) => {
        if (updated.some((item) => item.kind === "file" && item.path === pendingPath)) {
          setPendingPath(null); setUploadState("idle"); setProgress(100); setMessage(`${pendingPath} is published and ready for TV refresh.`);
        }
      }).catch(() => undefined);
      if (attempts >= 86) { window.clearInterval(timer); setPendingPath(null); setUploadState("error"); setMessage("GitHub did not publish the source within five minutes. Check the Publish TV source workflow."); }
    }, 3500);
    return () => window.clearInterval(timer);
  }, [pendingPath, refreshSources]);

  async function addSource(file: File | null) {
    if (!file) return;
    try {
      const filename = assertSourceFilename(file.name);
      assertSourceSize(file.size);
      const path = childPath(folder, filename);
      if (files.some((item) => item.path === path)) throw new SourceValidationError("A source with that path already exists.");
      const requestId = crypto.randomUUID().replace(/-/g, "");
      setUploadState("uploading"); setProgress(0); setMessage(`Uploading ${path}…`);
      await upload(`pending/${requestId}/${path}`, file, {
        access: "public", handleUploadUrl: "/api/uploads", clientPayload: JSON.stringify({ path, requestId }), multipart: file.size > 4 * 1024 * 1024,
        onUploadProgress: ({ percentage }) => setProgress(Math.round(percentage)),
      });
      setPendingPath(path); setUploadState("queued"); setMessage(`${path} uploaded. Waiting for GitHub to publish it…`);
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "The upload could not be started."); }
  }

  async function createFolder() {
    const name = window.prompt("Folder name", "");
    if (!name) return;
    try {
      const path = childPath(folder, name);
      const response = await fetch("/api/sources", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not create the folder.");
      await refreshSources(); setMessage(`Created ${path}.`);
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not create the folder."); }
  }

  async function moveToFolder(source: SourceRecord, destination: string) {
    const target = childPath(destination, source.name);
    if (target === source.path) return;
    try {
      setMessage(`Moving ${source.path}…`);
      const response = await fetch("/api/sources", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fromPath: source.path, toPath: target, sha: source.sha }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not move the source.");
      await refreshSources(); setMessage(`Moved to ${target}.`);
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not move the source."); }
  }

  async function removeItem(source: SourceRecord | FolderItem) {
    const kind = "kind" in source ? source.kind : "folder";
    const path = source.path;
    const sha = source.sha;
    if (!sha) { setMessage("This folder contains sources. Remove or move its contents first."); return; }
    if (!window.confirm(`Remove ${path}?`)) return;
    try {
      setDeleting(path);
      const response = await fetch("/api/sources", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, path, sha }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "The item could not be removed.");
      await refreshSources(); setMessage(`${path} was removed.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "The item could not be removed."); } finally { setDeleting(null); }
  }

  function toggleReceiver(sourcePath: string, receiverId: string) {
    setSelectedReceiverIds((current) => {
      const selected = current[sourcePath] || [];
      const next = selected.includes(receiverId)
        ? selected.filter((id) => id !== receiverId)
        : [...selected, receiverId];
      return { ...current, [sourcePath]: next };
    });
  }

  async function stageSource(receiverIds: string[], source: SourceRecord) {
    if (!receiverIds.length) return;
    try {
      setStaging(source.path); setMessage(`Staging ${source.name} to ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}…`);
      await Promise.all(receiverIds.map(async (receiverId) => {
        const response = await fetch("/api/receivers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ receiverId, sourcePath: source.path }) });
        const payload = await response.json() as { error?: string };
        if (!response.ok) throw new Error(payload.error || "Could not stage the source.");
      }));
      setSelectedReceiverIds((current) => ({ ...current, [source.path]: [] }));
      setMessage(`${source.name} staged for ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}. Each receiver checks again in about 30 seconds.`); void refreshReceivers();
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not stage the source."); } finally { setStaging(null); }
  }

  async function stagePictureInPicture(receiverIds: string[]) {
    if (!baseSource || !overlaySource || !receiverIds.length) return;
    if (baseSource.path === overlaySource.path) {
      setUploadState("error"); setMessage("Choose two different sources for picture-in-picture."); return;
    }
    try {
      setStaging(PICTURE_IN_PICTURE_SELECTION);
      setMessage(`Staging picture-in-picture to ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}…`);
      await Promise.all(receiverIds.map(async (receiverId) => {
        const response = await fetch("/api/receivers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kind: "picture-in-picture",
            receiverId,
            baseSourcePath: baseSource.path,
            overlaySourcePath: overlaySource.path,
            layout: pictureInPictureLayout,
          }),
        });
        const payload = await response.json() as { error?: string };
        if (!response.ok) throw new Error(payload.error || "Could not stage picture-in-picture.");
      }));
      setSelectedReceiverIds((current) => ({ ...current, [PICTURE_IN_PICTURE_SELECTION]: [] }));
      setMessage(`Picture-in-picture staged for ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}. Each receiver checks again in about 30 seconds.`);
      void refreshReceivers();
    } catch (error) {
      setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not stage picture-in-picture.");
    } finally {
      setStaging(null);
    }
  }

  function beginPictureInPictureDrag(event: React.PointerEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    event.preventDefault();
    setPictureInPictureDrag({
      pointerId: event.pointerId,
      mode: target.closest(".pip-resize-handle") ? "resize" : "move",
      originX: event.clientX,
      originY: event.clientY,
      layout: pictureInPictureLayout,
    });
  }

  function dragSource(event: React.DragEvent, source: SourceRecord) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-tv-source", JSON.stringify({ path: source.path, sha: source.sha }));
  }
  function draggedSource(event: React.DragEvent): SourceRecord | null {
    try {
      const payload = JSON.parse(event.dataTransfer.getData("application/x-tv-source")) as { path?: string; sha?: string };
      return files.find((item) => item.path === payload.path && item.sha === payload.sha) || null;
    } catch { return null; }
  }

  function choosePictureInPictureSource(source: SourceRecord) {
    if (!sourceIsPictureInPictureReady(source)) {
      setUploadState("error"); setMessage("Picture-in-picture accepts image and video sources only."); return;
    }
    if (!baseSourcePath) {
      setBaseSourcePath(source.path);
      setMessage(`${source.name} selected as the base source.`);
      return;
    }
    if (!overlaySourcePath && source.path !== baseSourcePath) {
      setOverlaySourcePath(source.path);
      setMessage(`${source.name} selected as the picture-in-picture source.`);
      return;
    }
    if (source.path === baseSourcePath) {
      setUploadState("error"); setMessage("Choose a different image or video for picture-in-picture."); return;
    }
    setOverlaySourcePath(source.path);
    setMessage(`${source.name} replaced the picture-in-picture source.`);
  }

  function dragOverPictureInPicture(event: React.DragEvent<HTMLDivElement>) {
    const types = Array.from(event.dataTransfer.types);
    if (!types.includes("application/x-tv-source") && !types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function dropPictureInPictureSource(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const source = draggedSource(event);
    if (!source) {
      dropExternalPictureInPictureImage(event); return;
    }
    choosePictureInPictureSource(source);
  }

  async function addExternalPictureInPictureImage(file: File, slot: "base" | "overlay") {
    let previewUrl = "";
    try {
      if (!sourceIsImageFile(file)) throw new SourceValidationError("Drag an image file (JPG, PNG, GIF, BMP, or WebP) onto the TV preview.");
      const filename = assertSourceFilename(file.name);
      assertSourceSize(file.size);
      const path = filename;
      if (files.some((item) => item.path === path) || pendingPictureInPictureUploads.some((item) => item.path === path)) {
        throw new SourceValidationError("A source with that name already exists. Rename the image before dropping it.");
      }
      previewUrl = URL.createObjectURL(file);
      pictureInPicturePreviewUrlsRef.current.add(previewUrl);
      setPendingPictureInPictureUploads((current) => {
        for (const item of current.filter((candidate) => candidate.slot === slot)) {
          pictureInPicturePreviewUrlsRef.current.delete(item.previewUrl);
          URL.revokeObjectURL(item.previewUrl);
        }
        return [...current.filter((candidate) => candidate.slot !== slot), { path, slot, previewUrl, name: filename }];
      });
      setUploadState("uploading"); setProgress(0); setMessage(`Uploading ${filename} for picture-in-picture…`);
      const requestId = crypto.randomUUID().replace(/-/g, "");
      await upload(`pending/${requestId}/${path}`, file, {
        access: "public",
        handleUploadUrl: "/api/uploads",
        clientPayload: JSON.stringify({ path, requestId }),
        multipart: file.size > 4 * 1024 * 1024,
        onUploadProgress: ({ percentage }) => setProgress(Math.round(percentage)),
      });
      setUploadState("queued"); setMessage(`${filename} is in the TV preview and is being published to sources/.`);
      let attempts = 0;
      const timer = window.setInterval(() => {
        attempts += 1;
        void refreshSources().then((updated) => {
          if (!updated.some((item) => item.kind === "file" && item.path === path)) return;
          window.clearInterval(timer);
          pictureInPictureUploadTimersRef.current.delete(timer);
          setPendingPictureInPictureUploads((current) => {
            const pending = current.find((item) => item.previewUrl === previewUrl);
            if (pending) {
              pictureInPicturePreviewUrlsRef.current.delete(pending.previewUrl);
              URL.revokeObjectURL(pending.previewUrl);
            }
            return current.filter((item) => item.previewUrl !== previewUrl);
          });
          if (slot === "base") setBaseSourcePath(path); else setOverlaySourcePath(path);
          setUploadState("idle"); setProgress(100); setMessage(`${filename} is published and selected as the ${slot === "base" ? "base" : "picture-in-picture"} source.`);
        }).catch(() => undefined);
        if (attempts < 86) return;
        window.clearInterval(timer);
        pictureInPictureUploadTimersRef.current.delete(timer);
        setPendingPictureInPictureUploads((current) => {
          const pending = current.find((item) => item.previewUrl === previewUrl);
          if (pending) {
            pictureInPicturePreviewUrlsRef.current.delete(pending.previewUrl);
            URL.revokeObjectURL(pending.previewUrl);
          }
          return current.filter((item) => item.previewUrl !== previewUrl);
        });
        setUploadState("error"); setMessage(`${filename} was not published within five minutes. Check the Publish TV source workflow.`);
      }, 3500);
      pictureInPictureUploadTimersRef.current.add(timer);
    } catch (error) {
      if (previewUrl) {
        pictureInPicturePreviewUrlsRef.current.delete(previewUrl);
        URL.revokeObjectURL(previewUrl);
        setPendingPictureInPictureUploads((current) => current.filter((item) => item.previewUrl !== previewUrl));
      }
      setUploadState("error"); setMessage(error instanceof Error ? error.message : "The image could not be uploaded.");
    }
  }

  function dropExternalPictureInPictureImage(event: React.DragEvent<HTMLDivElement>) {
    if (Array.from(event.dataTransfer.types).includes("application/x-tv-source")) return;
    event.preventDefault();
    const file = event.dataTransfer.files.item(0);
    if (!file) {
      setUploadState("error"); setMessage("Drop an image file from your desktop onto the TV preview."); return;
    }
    const slot: "base" | "overlay" = !baseSourcePath && !pendingBaseSource ? "base" : "overlay";
    void addExternalPictureInPictureImage(file, slot);
  }

  const crumbs = folder ? folder.split("/") : [];
  const dashboardTabs = (
    <nav className="dashboard-tabs" aria-label="Dashboard views">
      <button className={activeTab === "library" ? "active" : ""} type="button" onClick={() => setActiveTab("library")}>Source library</button>
      <button className={activeTab === "picture-in-picture" ? "active" : ""} type="button" onClick={() => setActiveTab("picture-in-picture")}>Picture in picture</button>
    </nav>
  );
  const selectedPictureInPictureReceivers = selectedReceiverIds[PICTURE_IN_PICTURE_SELECTION] || [];

  if (activeTab === "picture-in-picture") {
    return (
      <section className="source-manager">
        {dashboardTabs}
        <section className="pip-card">
          <div className="pip-heading">
            <div><p className="eyebrow">TV composition</p><h2>Picture in picture</h2><p>Choose a source, or drag an image from your desktop onto the TV preview. The first drop is the base and the second is picture in picture.</p></div>
            <button className="button secondary" type="button" onClick={() => setPictureInPictureLayout(DEFAULT_PICTURE_IN_PICTURE_LAYOUT)}>Reset layout</button>
          </div>
          {pictureInPictureFiles.length < 2 ? <p className="form-error">Add at least two image or video sources before creating picture-in-picture.</p> : null}
          <div className="pip-workspace">
            <div className="pip-controls">
              <label>Base source<select value={baseSourcePath} onChange={(event) => setBaseSourcePath(event.target.value)}><option value="">Select the full-screen source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === overlaySourcePath}>{source.path}</option>)}</select></label>
              <label>Picture in picture<select value={overlaySourcePath} onChange={(event) => setOverlaySourcePath(event.target.value)}><option value="">Select the overlay source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === baseSourcePath}>{source.path}</option>)}</select></label>
              <div className="pip-position-readout"><span>Position</span><code>{Math.round(pictureInPictureLayout.x * 100)}% × {Math.round(pictureInPictureLayout.y * 100)}%</code><span>Size</span><code>{Math.round(pictureInPictureLayout.width * 100)}% × {Math.round(pictureInPictureLayout.height * 100)}%</code></div>
            </div>
            <PictureInPicturePreview base={basePreviewSource} overlay={overlayPreviewSource} layout={pictureInPictureLayout} previewRef={pictureInPicturePreviewRef} onOverlayPointerDown={beginPictureInPictureDrag} onSourceDragOver={dragOverPictureInPicture} onSourceDrop={dropPictureInPictureSource} />
          </div>
          <section className="pip-source-shelf" aria-label="Drag sources onto the composition">
            <div><h3>Composition sources</h3><p>Drag an image or video to the TV preview, or click it to select it.</p></div>
            <div className="pip-source-list">
              {pictureInPictureFiles.map((source) => <button key={source.path} className={`pip-source-chip${source.path === baseSourcePath ? " base" : ""}${source.path === overlaySourcePath ? " overlay" : ""}`} type="button" draggable onDragStart={(event) => dragSource(event, source)} onClick={() => choosePictureInPictureSource(source)}><span className="drag-handle" aria-hidden="true">⠿</span><span className="pip-source-kind">{sourcePreviewKind(source) === "image" ? "Image" : "Video"}</span><span>{source.path}</span></button>)}
            </div>
          </section>
          <div className="pip-send-row">
            <p>{baseSource && overlaySource ? <><strong>{baseSource.name}</strong> as base with <strong>{overlaySource.name}</strong> as picture in picture.</> : pendingPictureInPictureUploads.length ? "Dropped image preview is visible. Send To unlocks once it is published to sources/." : "Choose a base source and a second picture-in-picture source."}</p>
            <details className="push-menu pip-send-menu">
              <summary className="button secondary">Send To{selectedPictureInPictureReceivers.length ? ` (${selectedPictureInPictureReceivers.length})` : ""} <span aria-hidden="true">⌄</span></summary>
              <div className="push-options">
                {receivers.length ? <>
                  {receivers.map((receiver) => {
                    const checked = selectedPictureInPictureReceivers.includes(receiver.id);
                    return <label key={receiver.id} className="push-option">
                      <input type="checkbox" checked={checked} disabled={staging !== null} onChange={() => toggleReceiver(PICTURE_IN_PICTURE_SELECTION, receiver.id)} />
                      <span className={`receiver-dot ${receiver.online ? "online" : "offline"}`} aria-hidden="true" />
                      <span className="push-receiver-info"><strong>{receiver.label}</strong><small title={receiver.lastSeenAt || undefined}>{relativeTime(receiver.lastSeenAt, now)} · {nextExpectedCheck(receiver.lastSeenAt, receiver.pollIntervalMs, now)}</small></span>
                    </label>;
                  })}
                  <button className="button push-submit" type="button" disabled={!baseSource || !overlaySource || !selectedPictureInPictureReceivers.length || staging !== null} onClick={() => void stagePictureInPicture(selectedPictureInPictureReceivers)}>{staging === PICTURE_IN_PICTURE_SELECTION ? "Sending…" : `Send to ${selectedPictureInPictureReceivers.length} TV${selectedPictureInPictureReceivers.length === 1 ? "" : "s"}`}</button>
                </> : <span className="push-loading">Loading TVs…</span>}
              </div>
            </details>
          </div>
          {message ? <p className={uploadState === "error" ? "form-error" : "status-message"}>{message}</p> : null}
        </section>
      </section>
    );
  }
  return (
    <section className="source-manager">
      {dashboardTabs}
      <section className="summary-card">
        <div><strong>{files.length}</strong><span>published sources</span></div>
        <div><strong>{folders.length}</strong><span>source folders</span></div>
        <div><strong>{formatBytes(totalSize)}</strong><span>current library size</span></div>
        <div><strong>{formatBytes(SOURCE_MAX_BYTES)}</strong><span>maximum per file</span></div>
      </section>

      <section className="upload-card">
        <div><h2>Add a source</h2><p>Current folder: <code>sources/{folder || ""}</code>. Drag published files onto a folder to move them, or use a source&apos;s Push To menu to stage it on a TV.</p></div>
        <div className="upload-actions"><button className="button secondary" type="button" onClick={() => void createFolder()}>New folder</button><label className="file-picker"><span>Choose media or document</span><input type="file" accept=".mp4,.mov,.m4v,.webm,.mp3,.wav,.ogg,.jpg,.jpeg,.png,.gif,.bmp,.webp,.pdf,.ppt,.pptx" disabled={uploadState === "uploading" || uploadState === "queued"} onChange={(event) => { void addSource(event.target.files?.[0] || null); event.currentTarget.value = ""; }} /></label></div>
        {uploadState === "uploading" || uploadState === "queued" ? <progress value={progress} max="100" /> : null}
        {message ? <p className={uploadState === "error" ? "form-error" : "status-message"}>{message}</p> : null}
      </section>

      <section className="table-card">
        <div className="table-heading"><div><h2>Published library</h2><nav className="breadcrumbs"><button type="button" onClick={() => setFolder("")}>sources</button>{crumbs.map((crumb, index) => { const path = crumbs.slice(0, index + 1).join("/"); return <span key={path}> / <button type="button" onClick={() => setFolder(path)}>{crumb}</button></span>; })}</nav></div><button className="button secondary" type="button" onClick={() => void refreshSources()}>Refresh</button></div>
        <div className="folder-grid">
          {folder ? <button className="folder-card parent-folder" type="button" onClick={() => setFolder(parentPath(folder))}>↩ <span>Up one folder</span></button> : null}
          {visibleFolders.map((item) => <div key={item.path} className="folder-card" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const source = draggedSource(event); if (source) void moveToFolder(source, item.path); }}><button type="button" onClick={() => setFolder(item.path)}>📁 <span>{item.name}</span></button><button className="icon-button" type="button" disabled={!item.sha || deleting === item.path} onClick={() => void removeItem(item)} aria-label={`Delete ${item.path}`}>×</button></div>)}
        </div>
        {visibleFiles.length ? (
          <div className="source-table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Revision</th><th>Preview</th><th>Push to</th><th /></tr></thead>
              <tbody>{visibleFiles.map((source) => {
                const selected = selectedReceiverIds[source.path] || [];
                const isStaging = staging === source.path;
                return (
                  <tr key={source.sha} draggable onDragStart={(event) => dragSource(event, source)}>
                    <td title="Drag to a folder"><span className="drag-handle">⠿</span>{source.name}</td>
                    <td>{source.name.split(".").pop()?.toUpperCase()}</td>
                    <td>{formatBytes(source.size)}</td>
                    <td><code>{source.sha.slice(0, 10)}</code></td>
                    <td>{source.downloadUrl ? <a href={source.downloadUrl} target="_blank" rel="noreferrer">Open</a> : "—"}</td>
                    <td>
                      <details className="push-menu">
                        <summary className="button secondary">Push To{selected.length ? ` (${selected.length})` : ""} <span aria-hidden="true">⌄</span></summary>
                        <div className="push-options">
                          {receivers.length ? <>
                            {receivers.map((receiver) => {
                              const checked = selected.includes(receiver.id);
                              return <label key={receiver.id} className="push-option">
                                <input type="checkbox" checked={checked} disabled={staging !== null} onChange={() => toggleReceiver(source.path, receiver.id)} />
                                <span className={`receiver-dot ${receiver.online ? "online" : "offline"}`} aria-hidden="true" />
                                <span className="push-receiver-info"><strong>{receiver.label}</strong><small title={receiver.lastSeenAt || undefined}>{relativeTime(receiver.lastSeenAt, now)} · {nextExpectedCheck(receiver.lastSeenAt, receiver.pollIntervalMs, now)}</small></span>
                              </label>;
                            })}
                            <button className="button push-submit" type="button" disabled={!selected.length || staging !== null} onClick={() => void stageSource(selected, source)}>{isStaging ? "Pushing…" : `Push to ${selected.length} TV${selected.length === 1 ? "" : "s"}`}</button>
                          </> : <span className="push-loading">Loading TVs…</span>}
                        </div>
                      </details>
                    </td>
                    <td><button className="button danger" type="button" disabled={deleting === source.path} onClick={() => void removeItem(source)}>{deleting === source.path ? "Removing…" : "Delete"}</button></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        ) : <p className="empty-state">No media files in this folder.</p>}
      </section>
    </section>
  );
}
