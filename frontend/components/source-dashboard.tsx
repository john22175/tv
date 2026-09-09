"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import type { SourceRecord } from "@/lib/github";
import {
  assertSourceFilename,
  assertSourceSize,
  PICTURE_IN_PICTURE_DIRECTORY,
  PICTURE_IN_PICTURE_OVERLAY_FILENAME,
  pictureInPictureOverlayPath,
  pictureInPictureRecipePath,
  isPresentationPath,
  isSlideShowRecipePath,
  slideShowRecipePathForPresentation,
  SOURCE_MAX_BYTES,
  SourceValidationError,
} from "@/lib/sources";

type UploadState = "idle" | "uploading" | "error";
type Receiver = { id: string; label: string; host: string; commandRevision: string | null; stagedAt: string | null; pollIntervalMs: number };
type FolderItem = { path: string; name: string; sha: string | null };
type DashboardTab = "library" | "picture-in-picture";
type PictureInPictureLayout = { x: number; y: number; width: number; height: number };
type PictureInPictureDrag = { pointerId: number; mode: "move" | "resize"; originX: number; originY: number; layout: PictureInPictureLayout };
type BackgroundRemoval = { color: string; tolerance: number };
type DirectGitHubUpload = { contentUrl: string; branch: string; path: string; token: string; existingSha?: string; error?: string };

const DEFAULT_PICTURE_IN_PICTURE_LAYOUT: PictureInPictureLayout = { x: 0.64, y: 0.06, width: 0.3, height: 0.3 };
const PICTURE_IN_PICTURE_SELECTION = "picture-in-picture";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
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

function isPictureInPictureSource(source: SourceRecord): boolean {
  return ["mp4", "mov", "m4v", "webm", "jpg", "jpeg", "png", "gif", "bmp", "webp"].includes(source.name.split(".").at(-1)?.toLowerCase() || "");
}

function isImageSource(source: Pick<SourceRecord, "name">): boolean {
  return ["jpg", "jpeg", "png", "gif", "bmp", "webp"].includes(source.name.split(".").at(-1)?.toLowerCase() || "");
}

function presentationStatus(source: SourceRecord, allFiles: SourceRecord[]): "processing" | "ready" {
  return allFiles.some((item) => item.path === slideShowRecipePathForPresentation(source.path)) ? "ready" : "processing";
}

function colorChannels(color: string): [number, number, number] {
  const match = /^#?([0-9a-f]{6})$/i.exec(color.trim());
  const value = match?.[1] || "ffffff";
  return [Number.parseInt(value.slice(0, 2), 16), Number.parseInt(value.slice(2, 4), 16), Number.parseInt(value.slice(4, 6), 16)];
}

function removeColorFromCanvas(canvas: HTMLCanvasElement, image: HTMLImageElement, removal: BackgroundRemoval) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context || !image.naturalWidth || !image.naturalHeight) return;
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
  const [red, green, blue] = colorChannels(removal.color);
  for (let index = 0; index < pixels.data.length; index += 4) {
    if (
      Math.abs(pixels.data[index] - red) <= removal.tolerance
      && Math.abs(pixels.data[index + 1] - green) <= removal.tolerance
      && Math.abs(pixels.data[index + 2] - blue) <= removal.tolerance
    ) {
      pixels.data[index + 3] = 0;
    }
  }
  context.putImageData(pixels, 0, 0);
}

function BackgroundRemovedImage({ source, className, alt, removal }: { source: SourceRecord; className?: string; alt: string; removal: BackgroundRemoval }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let disposed = false;
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => {
      if (disposed) return;
      try {
        removeColorFromCanvas(canvas, image, removal);
      } catch {
        // If a remote host disallows canvas reads, leave a blank preview. The
        // saved TV recipe still applies the same effect from GitHub raw media.
      }
    };
    image.src = source.downloadUrl;
    return () => { disposed = true; };
  }, [removal, source.downloadUrl]);
  return <canvas ref={canvasRef} className={className} role="img" aria-label={alt} />;
}

function MediaPreview({ source, className, alt }: { source: SourceRecord; className?: string; alt: string }) {
  return isImageSource(source)
    ? <img className={className} src={source.downloadUrl} alt={alt} />
    : <video className={className} src={source.downloadUrl} muted playsInline preload="metadata" aria-label={alt} />;
}

function receiverCommandSummary(receiver: Receiver): string {
  const interval = `Checks for commands every ${Math.round(receiver.pollIntervalMs / 1000)}s`;
  return receiver.stagedAt
    ? `${interval} - command staged ${new Date(receiver.stagedAt).toLocaleString()}`
    : `${interval} - no command staged`;
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
  if (!response.ok || !payload.receivers) throw new Error(payload.error || "Could not load TV commands.");
  return payload.receivers;
}

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Could not read the selected file."));
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      const comma = dataUrl.indexOf(",");
      if (comma < 0) return reject(new Error("Could not encode the selected file."));
      resolve(dataUrl.slice(comma + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** Convert every pasted image to one predictable PNG that can be overwritten safely. */
function clipboardImageAsReusableOverlay(file: File): Promise<File> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d");
      if (!context || !canvas.width || !canvas.height) {
        reject(new Error("The clipboard image could not be prepared."));
        return;
      }
      context.drawImage(image, 0, 0);
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error("The clipboard image could not be converted to PNG."));
          return;
        }
        resolve(new File([blob], PICTURE_IN_PICTURE_OVERLAY_FILENAME, { type: "image/png" }));
      }, "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("The clipboard image could not be read."));
    };
    image.src = objectUrl;
  });
}

async function directGitHubUpload(file: File, path: string, onProgress: (percentage: number) => void, overwrite = false): Promise<void> {
  const authorization = await fetch("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, size: file.size, overwrite }),
  });
  const session = await authorization.json() as DirectGitHubUpload;
  if (!authorization.ok || !session.contentUrl || !session.token || !session.branch || session.path !== path) {
    throw new Error(session.error || "Could not authorize the upload.");
  }

  onProgress(8);
  const content = await fileBase64(file);
  onProgress(30);
  await new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", session.contentUrl);
    request.setRequestHeader("Accept", "application/vnd.github+json");
    request.setRequestHeader("Authorization", `Bearer ${session.token}`);
    request.setRequestHeader("X-GitHub-Api-Version", "2022-11-28");
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(30 + Math.round((event.loaded / event.total) * 69));
    };
    request.onerror = () => reject(new Error("The upload could not reach GitHub."));
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve();
        return;
      }
      try {
        const payload = JSON.parse(request.responseText) as { message?: string };
        reject(new Error(payload.message || `GitHub upload failed (${request.status}).`));
      } catch {
        reject(new Error(`GitHub upload failed (${request.status}).`));
      }
    };
    request.send(JSON.stringify({ message: `source-manager: add ${path}`, content, branch: session.branch, ...(session.existingSha ? { sha: session.existingSha } : {}) }));
  });
}

function ReceiverPicker({
  receivers,
  receiverError,
  selected,
  disabled,
  selectionKey,
  onToggle,
  onRefresh,
}: {
  receivers: Receiver[];
  receiverError: string;
  selected: string[];
  disabled: boolean;
  selectionKey: string;
  onToggle: (selectionKey: string, receiverId: string) => void;
  onRefresh: () => void;
}) {
  if (receiverError) {
    return <div className="push-options"><span className="form-error">{receiverError}</span><button className="button secondary" type="button" onClick={onRefresh}>Retry TVs</button></div>;
  }
  if (!receivers.length) return <div className="push-options"><span className="push-loading">Loading TVs...</span></div>;
  return <div className="push-options">
    {receivers.map((receiver) => <label key={receiver.id} className="push-option">
      <input type="checkbox" checked={selected.includes(receiver.id)} disabled={disabled} onChange={() => onToggle(selectionKey, receiver.id)} />
      <span className="push-receiver-info"><strong>{receiver.label}</strong><small title={receiver.stagedAt || undefined}>{receiverCommandSummary(receiver)}</small></span>
    </label>)}
  </div>;
}

export function SourceDashboard({ initialSources }: { initialSources: SourceRecord[] }) {
  const [sources, setSources] = useState(initialSources);
  const [activeTab, setActiveTab] = useState<DashboardTab>("library");
  const [folder, setFolder] = useState("");
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [receivers, setReceivers] = useState<Receiver[]>([]);
  const [receiverError, setReceiverError] = useState("");
  const [staging, setStaging] = useState<string | null>(null);
  const [selectedReceiverIds, setSelectedReceiverIds] = useState<Record<string, string[]>>({});
  const [baseSourcePath, setBaseSourcePath] = useState("");
  const [overlaySourcePath, setOverlaySourcePath] = useState("");
  const [pictureInPictureLayout, setPictureInPictureLayout] = useState<PictureInPictureLayout>(DEFAULT_PICTURE_IN_PICTURE_LAYOUT);
  const [removeOverlayBackground, setRemoveOverlayBackground] = useState(false);
  const [overlayBackgroundColor, setOverlayBackgroundColor] = useState("#ffffff");
  const [pictureInPictureDrag, setPictureInPictureDrag] = useState<PictureInPictureDrag | null>(null);
  const pictureInPicturePreviewRef = useRef<HTMLDivElement>(null);

  const allFiles = useMemo(() => sources.filter((item) => item.kind === "file"), [sources]);
  // Slide manifests are internal helpers. Users select the original PowerPoint;
  // the receiver follows its manifest once GitHub has rendered the slides.
  const files = useMemo(() => allFiles.filter((item) => !isSlideShowRecipePath(item.path)), [allFiles]);
  const totalSize = useMemo(() => files.reduce((total, item) => total + item.size, 0), [files]);
  const refreshSources = useCallback(async () => {
    const updated = await apiSources();
    setSources(updated);
    return updated;
  }, []);
  const refreshReceivers = useCallback(async () => {
    try {
      setReceivers(await apiReceivers());
      setReceiverError("");
    } catch (error) {
      setReceiverError(error instanceof Error ? error.message : "Could not load TV commands.");
    }
  }, []);

  useEffect(() => { void refreshReceivers(); }, [refreshReceivers]);
  useEffect(() => {
    if (!pictureInPictureDrag) return;
    const drag = pictureInPictureDrag;
    function move(event: PointerEvent) {
      if (event.pointerId !== drag.pointerId) return;
      const preview = pictureInPicturePreviewRef.current;
      if (!preview) return;
      const bounds = preview.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const deltaX = (event.clientX - drag.originX) / bounds.width;
      const deltaY = (event.clientY - drag.originY) / bounds.height;
      setPictureInPictureLayout(() => {
        if (drag.mode === "resize") {
          const aspectRatio = Math.max(0.1, drag.layout.width / drag.layout.height);
          const widthDelta = Math.abs(deltaX) >= Math.abs(deltaY * aspectRatio) ? deltaX : deltaY * aspectRatio;
          const minimumWidth = Math.max(0.12, 0.12 * aspectRatio);
          const maximumWidth = Math.min(0.88, 1 - drag.layout.x, (1 - drag.layout.y) * aspectRatio);
          const width = clamp(drag.layout.width + widthDelta, minimumWidth, maximumWidth);
          return { ...drag.layout, width, height: width / aspectRatio };
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

  const folders = useMemo(() => {
    const known = new Map<string, FolderItem>();
    for (const source of [...sources].filter((item) => !isSlideShowRecipePath(item.path))) {
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
  const visibleFolders = useMemo(() => folders.filter((item) => parentPath(item.path) === folder), [folder, folders]);
  const visibleFiles = useMemo(() => files.filter((item) => parentPath(item.path) === folder).sort((left, right) => left.name.localeCompare(right.name)), [files, folder]);
  const pictureInPictureFiles = useMemo(() => files.filter(isPictureInPictureSource).sort((left, right) => left.path.localeCompare(right.path)), [files]);
  const baseSource = useMemo(() => pictureInPictureFiles.find((item) => item.path === baseSourcePath), [baseSourcePath, pictureInPictureFiles]);
  const overlaySource = useMemo(() => pictureInPictureFiles.find((item) => item.path === overlaySourcePath), [overlaySourcePath, pictureInPictureFiles]);

  async function addSource(file: File | null, selectFor?: "base" | "overlay", destinationFolder = folder, overwrite = false) {
    if (!file) return;
    try {
      const filename = assertSourceFilename(file.name);
      assertSourceSize(file.size);
      const path = childPath(destinationFolder, filename);
      if (files.some((item) => item.path === path) && !overwrite) throw new SourceValidationError("A source with that path already exists.");
      if (file.size > 50 * 1024 * 1024 && !window.confirm("This file is larger than 50 MiB. Direct GitHub upload Base64-encodes it in this browser, which can use substantial memory. Continue?")) return;
      setUploadState("uploading"); setProgress(0); setMessage(`Uploading ${path}...`);
      await directGitHubUpload(file, path, setProgress, overwrite);
      await refreshSources();
      if (selectFor === "base") setBaseSourcePath(path);
      if (selectFor === "overlay") setOverlaySourcePath(path);
      setUploadState("idle"); setProgress(100); setMessage(isPresentationPath(path)
        ? `${path} is published. GitHub is rendering its slides now; refresh until it is marked Ready to loop.`
        : `${path} is published and ready to stage.`);
    } catch (error) {
      setUploadState("error"); setMessage(error instanceof Error ? error.message : "The upload could not be started.");
    }
  }

  useEffect(() => {
    if (activeTab !== "picture-in-picture") return;
    function pasteClipboardImage(event: ClipboardEvent) {
      const focused = document.activeElement;
      if (focused instanceof HTMLInputElement || focused instanceof HTMLTextAreaElement || focused instanceof HTMLSelectElement) return;
      const item = Array.from(event.clipboardData?.items || []).find((candidate) => candidate.type.startsWith("image/"));
      const image = item?.getAsFile();
      if (!image) return;
      if (!["image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp"].includes(image.type.toLowerCase())) {
        setUploadState("error");
        setMessage("Clipboard images must be JPG, PNG, GIF, BMP, or WebP.");
        return;
      }
      event.preventDefault();
      void clipboardImageAsReusableOverlay(image)
        .then((file) => addSource(file, "overlay", PICTURE_IN_PICTURE_DIRECTORY, true))
        .catch((error) => {
          setUploadState("error");
          setMessage(error instanceof Error ? error.message : "The clipboard image could not be prepared.");
        });
    }
    window.addEventListener("paste", pasteClipboardImage);
    return () => window.removeEventListener("paste", pasteClipboardImage);
  }, [activeTab, files, folder, refreshSources]);

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
      setMessage(`Moving ${source.path}...`);
      const response = await fetch("/api/sources", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fromPath: source.path, toPath: target, sha: source.sha }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not move the source.");
      await refreshSources(); setMessage(`Moved to ${target}.`);
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not move the source."); }
  }

  async function removeItem(source: SourceRecord | FolderItem) {
    const kind = "kind" in source ? source.kind : "folder";
    if (!source.sha) { setMessage("This folder contains sources. Remove or move its contents first."); return; }
    if (!window.confirm(`Remove ${source.path}?`)) return;
    try {
      setDeleting(source.path);
      const response = await fetch("/api/sources", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, path: source.path, sha: source.sha }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "The item could not be removed.");
      await refreshSources(); setMessage(`${source.path} was removed.`);
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "The item could not be removed."); } finally { setDeleting(null); }
  }

  function toggleReceiver(selectionKey: string, receiverId: string) {
    setSelectedReceiverIds((current) => {
      const selected = current[selectionKey] || [];
      return { ...current, [selectionKey]: selected.includes(receiverId) ? selected.filter((id) => id !== receiverId) : [...selected, receiverId] };
    });
  }

  function beginPictureInPictureDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setPictureInPictureDrag({
      pointerId: event.pointerId,
      mode: target.closest(".pip-resize-handle") ? "resize" : "move",
      originX: event.clientX,
      originY: event.clientY,
      layout: pictureInPictureLayout,
    });
  }

  async function stageSource(receiverIds: string[], source: SourceRecord) {
    if (!receiverIds.length) return;
    try {
      setStaging(source.path); setMessage(`Staging ${source.name} to ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}...`);
      const response = await fetch("/api/receivers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ receiverIds, sourcePath: source.path }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not stage the source.");
      setSelectedReceiverIds((current) => ({ ...current, [source.path]: [] }));
      setMessage(`${source.name} staged for ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}. Each receiver checks again within about one minute.`);
      void refreshReceivers();
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not stage the source."); } finally { setStaging(null); }
  }

  async function saveAndStagePictureInPicture(receiverIds: string[]) {
    if (!baseSource || !overlaySource || !receiverIds.length) return;
    if (baseSource.path === overlaySource.path) { setUploadState("error"); setMessage("Choose two different sources for picture-in-picture."); return; }
    try {
      const target = pictureInPictureRecipePath();
      setStaging(PICTURE_IN_PICTURE_SELECTION); setMessage(`Saving ${target} and staging it to ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}...`);
      const response = await fetch("/api/receivers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "save-picture-in-picture", receiverIds, baseSourcePath: baseSource.path, overlaySourcePath: overlaySource.path, layout: pictureInPictureLayout, removeBackground: removeOverlayBackground ? { color: overlayBackgroundColor, tolerance: 32 } : null }),
      });
      const payload = await response.json() as { error?: string; recipe?: { path?: string; replacedPaths?: string[] } };
      if (!response.ok) throw new Error(payload.error || "Could not save picture-in-picture.");
      setSelectedReceiverIds((current) => ({ ...current, [PICTURE_IN_PICTURE_SELECTION]: [] }));
      await refreshSources();
      const replacement = payload.recipe?.replacedPaths?.length ? ` Replaced ${payload.recipe.replacedPaths.length} older PiP recipe${payload.recipe.replacedPaths.length === 1 ? "" : "s"} in that folder.` : "";
      setMessage(`${payload.recipe?.path || target} saved and staged for ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}.${replacement} Each receiver checks again within about one minute.`);
      void refreshReceivers();
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not save picture-in-picture."); } finally { setStaging(null); }
  }

  const crumbs = folder ? folder.split("/") : [];
  const selectedPictureInPictureReceivers = selectedReceiverIds[PICTURE_IN_PICTURE_SELECTION] || [];
  const dashboardTabs = <nav className="dashboard-tabs" aria-label="Dashboard views">
    <button className={activeTab === "library" ? "active" : ""} type="button" onClick={() => setActiveTab("library")}>Source library</button>
    <button className={activeTab === "picture-in-picture" ? "active" : ""} type="button" onClick={() => setActiveTab("picture-in-picture")}>Picture in picture</button>
  </nav>;

  if (activeTab === "picture-in-picture") {
    return <section className="source-manager">
      {dashboardTabs}
      <section className="pip-card">
        <div className="pip-heading"><div><p className="eyebrow">TV composition</p><h2>Picture in picture</h2><p>Select published sources, then save the reusable <strong>Welcome_Filled</strong> composition in <code>Welcome/Temp</code>. Press <kbd>Ctrl</kbd> + <kbd>V</kbd> here to replace its reusable overlay image.</p></div><button className="button secondary" type="button" onClick={() => setPictureInPictureLayout(DEFAULT_PICTURE_IN_PICTURE_LAYOUT)}>Reset layout</button></div>
        <div className="pip-workspace">
          <div className="pip-controls">
            <label>Base source<select value={baseSourcePath} onChange={(event) => setBaseSourcePath(event.target.value)}><option value="">Select full-screen source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === overlaySourcePath}>{source.path}</option>)}</select></label>
            <label>Picture in picture<select value={overlaySourcePath} onChange={(event) => setOverlaySourcePath(event.target.value)}><option value="">Select overlay source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === baseSourcePath}>{source.path}</option>)}</select></label>
            <p className="pip-fixed-path">Reusable PiP files are stored in <code>sources/{PICTURE_IN_PICTURE_DIRECTORY}/</code>: <code>{pictureInPictureOverlayPath()}</code> and <code>{pictureInPictureRecipePath()}</code>.</p>
            <label className="pip-checkbox"><input type="checkbox" checked={removeOverlayBackground} disabled={!overlaySource || !isImageSource(overlaySource)} onChange={(event) => setRemoveOverlayBackground(event.target.checked)} /> Remove Background</label>
            {removeOverlayBackground ? <label>Background color<input type="color" value={overlayBackgroundColor} onChange={(event) => setOverlayBackgroundColor(event.target.value)} /><small>Matching overlay-image pixels become transparent.</small></label> : null}
            <div className="pip-position-readout"><span>Position</span><code>{Math.round(pictureInPictureLayout.x * 100)}% x {Math.round(pictureInPictureLayout.y * 100)}%</code><span>Size</span><code>{Math.round(pictureInPictureLayout.width * 100)}% x {Math.round(pictureInPictureLayout.height * 100)}%</code></div>
          </div>
          <div ref={pictureInPicturePreviewRef} className="pip-preview" aria-label="Picture-in-picture preview"><div className="pip-preview-label">TV preview</div>{baseSource ? <MediaPreview className="pip-base" source={baseSource} alt={`Base: ${baseSource.name}`} /> : <div className="pip-empty">Choose a base source</div>}{overlaySource ? <div className={`pip-overlay-frame${removeOverlayBackground && isImageSource(overlaySource) ? " pip-overlay-frame-transparent" : ""}`} style={{ left: `${pictureInPictureLayout.x * 100}%`, top: `${pictureInPictureLayout.y * 100}%`, width: `${pictureInPictureLayout.width * 100}%`, height: `${pictureInPictureLayout.height * 100}%` }} onPointerDown={beginPictureInPictureDrag} role="presentation">{removeOverlayBackground && isImageSource(overlaySource) ? <BackgroundRemovedImage className="pip-overlay-image" source={overlaySource} alt={`Overlay: ${overlaySource.name}`} removal={{ color: overlayBackgroundColor, tolerance: 32 }} /> : <MediaPreview source={overlaySource} alt={`Overlay: ${overlaySource.name}`} />}<span className="pip-overlay-label">Picture in picture · drag to move</span><span className="pip-resize-handle" aria-label="Drag to resize" /></div> : null}</div>
        </div>
        <div className="pip-send-row"><p>{baseSource && overlaySource ? <><strong>{baseSource.name}</strong> as base with <strong>{overlaySource.name}</strong> as picture in picture. The saved source will be <strong>Welcome_Filled</strong>.</> : "Choose a base source and a second picture-in-picture source."}</p><details className="push-menu pip-send-menu"><summary className="button secondary">Save &amp; Stage{selectedPictureInPictureReceivers.length ? ` (${selectedPictureInPictureReceivers.length})` : ""}</summary><ReceiverPicker receivers={receivers} receiverError={receiverError} selected={selectedPictureInPictureReceivers} disabled={staging !== null} selectionKey={PICTURE_IN_PICTURE_SELECTION} onToggle={toggleReceiver} onRefresh={() => void refreshReceivers()} />{receivers.length && !receiverError ? <button className="button push-submit" type="button" disabled={!baseSource || !overlaySource || !selectedPictureInPictureReceivers.length || staging !== null} onClick={() => void saveAndStagePictureInPicture(selectedPictureInPictureReceivers)}>{staging === PICTURE_IN_PICTURE_SELECTION ? "Saving..." : `Save and stage for ${selectedPictureInPictureReceivers.length} TV${selectedPictureInPictureReceivers.length === 1 ? "" : "s"}`}</button> : null}</details></div>
        {uploadState === "uploading" ? <progress value={progress} max="100" /> : null}{message ? <p className={uploadState === "error" ? "form-error" : "status-message"}>{message}</p> : null}
      </section>
    </section>;
  }

  return <section className="source-manager">
    {dashboardTabs}
    <section className="summary-card"><div><strong>{files.length}</strong><span>published sources</span></div><div><strong>{folders.length}</strong><span>source folders</span></div><div><strong>{formatBytes(totalSize)}</strong><span>current library size</span></div><div><strong>{formatBytes(SOURCE_MAX_BYTES)}</strong><span>maximum per file</span></div></section>
    <section className="upload-card"><div><h2>Add a source</h2><p>Current folder: <code>sources/{folder || ""}</code>. Uploads go directly from this browser to the media repository; they are immediately available to stage.</p></div><div className="upload-actions"><button className="button secondary" type="button" onClick={() => void createFolder()}>New folder</button><label className="file-picker"><span>Choose media or document</span><input type="file" accept=".mp4,.mov,.m4v,.webm,.mp3,.wav,.ogg,.jpg,.jpeg,.png,.gif,.bmp,.webp,.pdf,.ppt,.pptx" disabled={uploadState === "uploading"} onChange={(event) => { void addSource(event.target.files?.[0] || null); event.currentTarget.value = ""; }} /></label></div>{uploadState === "uploading" ? <progress value={progress} max="100" /> : null}{message ? <p className={uploadState === "error" ? "form-error" : "status-message"}>{message}</p> : null}</section>
    <section className="table-card"><div className="table-heading"><div><h2>Published library</h2><nav className="breadcrumbs"><button type="button" onClick={() => setFolder("")}>sources</button>{crumbs.map((crumb, index) => { const path = crumbs.slice(0, index + 1).join("/"); return <span key={path}> / <button type="button" onClick={() => setFolder(path)}>{crumb}</button></span>; })}</nav></div><button className="button secondary" type="button" onClick={() => void refreshSources()}>Refresh</button></div><div className="folder-grid">{folder ? <button className="folder-card parent-folder" type="button" onClick={() => setFolder(parentPath(folder))}>Up one folder</button> : null}{visibleFolders.map((item) => <div key={item.path} className="folder-card"><button type="button" onClick={() => setFolder(item.path)}>Folder <span>{item.name}</span></button><button className="icon-button" type="button" disabled={!item.sha || deleting === item.path} onClick={() => void removeItem(item)} aria-label={`Delete ${item.path}`}>x</button></div>)}</div>
      {visibleFiles.length ? <div className="source-table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Revision</th><th>Preview</th><th>Push to</th><th /></tr></thead><tbody>{visibleFiles.map((source) => {
        const selected = selectedReceiverIds[source.path] || [];
        const presentation = isPresentationPath(source.path);
        const ready = !presentation || presentationStatus(source, allFiles) === "ready";
        return <tr key={source.sha}><td>{source.name}</td><td>{presentation ? `${source.name.split(".").pop()?.toUpperCase()} · ${ready ? "Ready to loop" : "Rendering slides…"}` : source.name.split(".").pop()?.toUpperCase()}</td><td>{formatBytes(source.size)}</td><td><code>{source.sha.slice(0, 10)}</code></td><td><a href={source.downloadUrl} target="_blank" rel="noreferrer">Open</a></td><td><details className="push-menu"><summary className="button secondary" aria-disabled={!ready}>{presentation && !ready ? "Rendering slides…" : `Push To${selected.length ? ` (${selected.length})` : ""}`}</summary>{ready ? <><ReceiverPicker receivers={receivers} receiverError={receiverError} selected={selected} disabled={staging !== null} selectionKey={source.path} onToggle={toggleReceiver} onRefresh={() => void refreshReceivers()} />{receivers.length && !receiverError ? <button className="button push-submit" type="button" disabled={!selected.length || staging !== null} onClick={() => void stageSource(selected, source)}>{staging === source.path ? "Pushing..." : `Push to ${selected.length} TV${selected.length === 1 ? "" : "s"}`}</button> : null}</> : <p className="push-options">GitHub is converting this presentation. Press Refresh shortly; it will loop on the TV once ready.</p>}</details></td><td><button className="button secondary" type="button" onClick={() => { const destination = window.prompt("Move to folder (leave blank for sources root)", parentPath(source.path)); if (destination !== null) void moveToFolder(source, destination.trim()); }}>Move</button> <button className="button danger" type="button" disabled={deleting === source.path} onClick={() => void removeItem(source)}>{deleting === source.path ? "Removing..." : "Delete"}</button></td></tr>;
      })}</tbody></table></div> : <p className="empty-state">No media files in this folder.</p>}
    </section>
  </section>;
}
