"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { SourceRecord } from "@/lib/github";
import { assertSourceFilename, assertSourceSize, SOURCE_MAX_BYTES, SourceValidationError } from "@/lib/sources";

type UploadState = "idle" | "uploading" | "error";
type Receiver = { id: string; label: string; host: string; commandRevision: string | null; stagedAt: string | null; pollIntervalMs: number };
type FolderItem = { path: string; name: string; sha: string | null };
type DashboardTab = "library" | "picture-in-picture";
type PictureInPictureLayout = { x: number; y: number; width: number; height: number };
type DirectGitHubUpload = { contentUrl: string; branch: string; path: string; token: string; error?: string };

const DEFAULT_PICTURE_IN_PICTURE_LAYOUT: PictureInPictureLayout = { x: 0.64, y: 0.06, width: 0.3, height: 0.3 };
const PICTURE_IN_PICTURE_SELECTION = "picture-in-picture";
const PICTURE_IN_PICTURE_RECIPE_FILENAME = "Welcome_Filled.pip.json";

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

async function directGitHubUpload(file: File, path: string, onProgress: (percentage: number) => void): Promise<void> {
  const authorization = await fetch("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, size: file.size }),
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
    request.send(JSON.stringify({ message: `source-manager: add ${path}`, content, branch: session.branch }));
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
  const [pictureInPictureFolder, setPictureInPictureFolder] = useState("");
  const [pictureInPictureLayout, setPictureInPictureLayout] = useState<PictureInPictureLayout>(DEFAULT_PICTURE_IN_PICTURE_LAYOUT);

  const files = useMemo(() => sources.filter((item) => item.kind === "file"), [sources]);
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
  const visibleFolders = useMemo(() => folders.filter((item) => parentPath(item.path) === folder), [folder, folders]);
  const visibleFiles = useMemo(() => files.filter((item) => parentPath(item.path) === folder).sort((left, right) => left.name.localeCompare(right.name)), [files, folder]);
  const pictureInPictureFiles = useMemo(() => files.filter(isPictureInPictureSource).sort((left, right) => left.path.localeCompare(right.path)), [files]);
  const baseSource = useMemo(() => pictureInPictureFiles.find((item) => item.path === baseSourcePath), [baseSourcePath, pictureInPictureFiles]);
  const overlaySource = useMemo(() => pictureInPictureFiles.find((item) => item.path === overlaySourcePath), [overlaySourcePath, pictureInPictureFiles]);

  async function addSource(file: File | null, selectFor?: "base" | "overlay") {
    if (!file) return;
    try {
      const filename = assertSourceFilename(file.name);
      assertSourceSize(file.size);
      const path = childPath(folder, filename);
      if (files.some((item) => item.path === path)) throw new SourceValidationError("A source with that path already exists.");
      if (file.size > 50 * 1024 * 1024 && !window.confirm("This file is larger than 50 MiB. Direct GitHub upload Base64-encodes it in this browser, which can use substantial memory. Continue?")) return;
      setUploadState("uploading"); setProgress(0); setMessage(`Uploading ${path}...`);
      await directGitHubUpload(file, path, setProgress);
      await refreshSources();
      if (selectFor === "base") setBaseSourcePath(path);
      if (selectFor === "overlay") setOverlaySourcePath(path);
      setUploadState("idle"); setProgress(100); setMessage(`${path} is published and ready to stage.`);
    } catch (error) {
      setUploadState("error"); setMessage(error instanceof Error ? error.message : "The upload could not be started.");
    }
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
      const target = childPath(pictureInPictureFolder, PICTURE_IN_PICTURE_RECIPE_FILENAME);
      setStaging(PICTURE_IN_PICTURE_SELECTION); setMessage(`Saving ${target} and staging it to ${receiverIds.length} TV${receiverIds.length === 1 ? "" : "s"}...`);
      const response = await fetch("/api/receivers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "save-picture-in-picture", receiverIds, baseSourcePath: baseSource.path, overlaySourcePath: overlaySource.path, destinationFolder: pictureInPictureFolder, layout: pictureInPictureLayout }),
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
    <button className={activeTab === "picture-in-picture" ? "active" : ""} type="button" onClick={() => { setPictureInPictureFolder(folder); setActiveTab("picture-in-picture"); }}>Picture in picture</button>
  </nav>;

  if (activeTab === "picture-in-picture") {
    return <section className="source-manager">
      {dashboardTabs}
      <section className="pip-card">
        <div className="pip-heading"><div><p className="eyebrow">TV composition</p><h2>Picture in picture</h2><p>Select a full-screen source and an overlay, then save the reusable composition as <strong>Welcome_Filled</strong> in a source folder. Saving replaces any older PiP recipes in that folder and stages the saved source normally.</p></div><button className="button secondary" type="button" onClick={() => setPictureInPictureLayout(DEFAULT_PICTURE_IN_PICTURE_LAYOUT)}>Reset layout</button></div>
        <div className="pip-workspace">
          <div className="pip-controls">
            <label>Base source<select value={baseSourcePath} onChange={(event) => setBaseSourcePath(event.target.value)}><option value="">Select full-screen source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === overlaySourcePath}>{source.path}</option>)}</select></label>
            <label>Picture in picture<select value={overlaySourcePath} onChange={(event) => setOverlaySourcePath(event.target.value)}><option value="">Select overlay source</option>{pictureInPictureFiles.map((source) => <option key={source.path} value={source.path} disabled={source.path === baseSourcePath}>{source.path}</option>)}</select></label>
            <label>Save in folder<select value={pictureInPictureFolder} onChange={(event) => setPictureInPictureFolder(event.target.value)}><option value="">sources (root)</option>{folders.map((item) => <option key={item.path} value={item.path}>{item.path}</option>)}</select><small>Creates <code>{childPath(pictureInPictureFolder, PICTURE_IN_PICTURE_RECIPE_FILENAME)}</code>.</small></label>
            <label className="file-picker"><span>Add image as base</span><input type="file" accept=".jpg,.jpeg,.png,.gif,.bmp,.webp" disabled={uploadState === "uploading"} onChange={(event) => { void addSource(event.target.files?.[0] || null, "base"); event.currentTarget.value = ""; }} /></label>
            <label className="file-picker"><span>Add image as overlay</span><input type="file" accept=".jpg,.jpeg,.png,.gif,.bmp,.webp" disabled={uploadState === "uploading"} onChange={(event) => { void addSource(event.target.files?.[0] || null, "overlay"); event.currentTarget.value = ""; }} /></label>
            <div className="pip-position-readout"><span>Position</span><code>{Math.round(pictureInPictureLayout.x * 100)}% x {Math.round(pictureInPictureLayout.y * 100)}%</code><span>Size</span><code>{Math.round(pictureInPictureLayout.width * 100)}% x {Math.round(pictureInPictureLayout.height * 100)}%</code></div>
          </div>
          <div className="pip-preview" aria-label="Picture-in-picture preview"><div className="pip-preview-label">TV preview</div>{baseSource ? <MediaPreview className="pip-base" source={baseSource} alt={`Base: ${baseSource.name}`} /> : <div className="pip-empty">Choose a base source</div>}{overlaySource ? <div className="pip-overlay-frame" style={{ left: `${pictureInPictureLayout.x * 100}%`, top: `${pictureInPictureLayout.y * 100}%`, width: `${pictureInPictureLayout.width * 100}%`, height: `${pictureInPictureLayout.height * 100}%` }}><MediaPreview source={overlaySource} alt={`Overlay: ${overlaySource.name}`} /><span className="pip-overlay-label">Picture in picture</span></div> : null}</div>
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
        return <tr key={source.sha}><td>{source.name}</td><td>{source.name.split(".").pop()?.toUpperCase()}</td><td>{formatBytes(source.size)}</td><td><code>{source.sha.slice(0, 10)}</code></td><td><a href={source.downloadUrl} target="_blank" rel="noreferrer">Open</a></td><td><details className="push-menu"><summary className="button secondary">Push To{selected.length ? ` (${selected.length})` : ""}</summary><ReceiverPicker receivers={receivers} receiverError={receiverError} selected={selected} disabled={staging !== null} selectionKey={source.path} onToggle={toggleReceiver} onRefresh={() => void refreshReceivers()} />{receivers.length && !receiverError ? <button className="button push-submit" type="button" disabled={!selected.length || staging !== null} onClick={() => void stageSource(selected, source)}>{staging === source.path ? "Pushing..." : `Push to ${selected.length} TV${selected.length === 1 ? "" : "s"}`}</button> : null}</details></td><td><button className="button secondary" type="button" onClick={() => { const destination = window.prompt("Move to folder (leave blank for sources root)", parentPath(source.path)); if (destination !== null) void moveToFolder(source, destination.trim()); }}>Move</button> <button className="button danger" type="button" disabled={deleting === source.path} onClick={() => void removeItem(source)}>{deleting === source.path ? "Removing..." : "Delete"}</button></td></tr>;
      })}</tbody></table></div> : <p className="empty-state">No media files in this folder.</p>}
    </section>
  </section>;
}
