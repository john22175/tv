"use client";

import { upload } from "@vercel/blob/client";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { SourceRecord } from "@/lib/github";
import { assertSourceFilename, assertSourceSize, SOURCE_MAX_BYTES, SourceValidationError } from "@/lib/sources";

type UploadState = "idle" | "uploading" | "queued" | "error";
type Receiver = { id: string; label: string; host: string; online: boolean; lastSeenAt: string | null; commandRevision: string | null };
type FolderItem = { path: string; name: string; sha: string | null };

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

function relativeTime(value: string | null): string {
  if (!value) return "No receiver heartbeat";
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 1000));
  if (seconds < 60) return "Listening now";
  return `Seen ${Math.floor(seconds / 60)}m ago`;
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

export function SourceDashboard({ initialSources }: { initialSources: SourceRecord[] }) {
  const [sources, setSources] = useState(initialSources);
  const [folder, setFolder] = useState("");
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [receivers, setReceivers] = useState<Receiver[]>([]);
  const [staging, setStaging] = useState<string | null>(null);

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

  useEffect(() => { void refreshReceivers(); const timer = window.setInterval(() => void refreshReceivers(), 30_000); return () => window.clearInterval(timer); }, [refreshReceivers]);
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

  async function stageSource(receiverId: string, source: SourceRecord) {
    try {
      setStaging(receiverId); setMessage(`Staging ${source.name}…`);
      const response = await fetch("/api/receivers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ receiverId, sourcePath: source.path }) });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not stage the source.");
      setMessage(`${source.name} staged. The TV will pick it up on its next listener check.`); void refreshReceivers();
    } catch (error) { setUploadState("error"); setMessage(error instanceof Error ? error.message : "Could not stage the source."); } finally { setStaging(null); }
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

  const crumbs = folder ? folder.split("/") : [];
  return (
    <section className="source-manager">
      <section className="summary-card">
        <div><strong>{files.length}</strong><span>published sources</span></div>
        <div><strong>{folders.length}</strong><span>source folders</span></div>
        <div><strong>{formatBytes(totalSize)}</strong><span>current library size</span></div>
        <div><strong>{formatBytes(SOURCE_MAX_BYTES)}</strong><span>maximum per file</span></div>
      </section>

      <section className="upload-card">
        <div><h2>Add a source</h2><p>Current folder: <code>sources/{folder || ""}</code>. Drag published files onto a folder or TV tile to move or stage them.</p></div>
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
        {visibleFiles.length ? <div className="source-table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Revision</th><th>Preview</th><th /></tr></thead><tbody>{visibleFiles.map((source) => <tr key={source.sha} draggable onDragStart={(event) => dragSource(event, source)}><td title="Drag to a folder or TV"><span className="drag-handle">⠿</span>{source.name}</td><td>{source.name.split(".").pop()?.toUpperCase()}</td><td>{formatBytes(source.size)}</td><td><code>{source.sha.slice(0, 10)}</code></td><td>{source.downloadUrl ? <a href={source.downloadUrl} target="_blank" rel="noreferrer">Open</a> : "—"}</td><td><button className="button danger" type="button" disabled={deleting === source.path} onClick={() => void removeItem(source)}>{deleting === source.path ? "Removing…" : "Delete"}</button></td></tr>)}</tbody></table></div> : <p className="empty-state">No media files in this folder.</p>}
      </section>

      <section className="receiver-board table-card">
        <div className="table-heading"><div><h2>TV stage</h2><p className="board-note">Green means the running receiver checked in recently. Drop a source onto a TV to stage it.</p></div><button className="button secondary" type="button" onClick={() => void refreshReceivers()}>Refresh TVs</button></div>
        <div className="receiver-grid">{receivers.map((receiver) => <div key={receiver.id} className={`receiver-tile${staging === receiver.id ? " staging" : ""}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const source = draggedSource(event); if (source) void stageSource(receiver.id, source); }}><div className="receiver-heading"><span className={`receiver-dot ${receiver.online ? "online" : "offline"}`} aria-label={receiver.online ? "Listening" : "Not listening"} /><strong>{receiver.label}</strong></div><span>{receiver.host}</span><small>{relativeTime(receiver.lastSeenAt)}</small></div>)}</div>
      </section>
    </section>
  );
}
