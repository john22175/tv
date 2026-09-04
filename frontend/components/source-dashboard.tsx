"use client";

import { upload } from "@vercel/blob/client";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { SourceRecord } from "@/lib/github";
import { assertSourceFilename, assertSourceSize, SOURCE_MAX_BYTES, SourceValidationError } from "@/lib/sources";

type UploadState = "idle" | "uploading" | "queued" | "error";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let index = -1;
  let size = value;
  do {
    size /= 1024;
    index += 1;
  } while (size >= 1024 && index < units.length - 1);
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

async function apiSources(): Promise<SourceRecord[]> {
  const response = await fetch("/api/sources", { cache: "no-store" });
  const payload = await response.json() as { sources?: SourceRecord[]; error?: string };
  if (!response.ok || !payload.sources) throw new Error(payload.error || "Could not refresh sources.");
  return payload.sources;
}

export function SourceDashboard({ initialSources }: { initialSources: SourceRecord[] }) {
  const [sources, setSources] = useState(initialSources);
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [pendingFilename, setPendingFilename] = useState<string | null>(null);

  const totalSize = useMemo(() => sources.reduce((total, item) => total + item.size, 0), [sources]);
  const refreshSources = useCallback(async () => {
    const updated = await apiSources();
    setSources(updated);
    return updated;
  }, []);

  useEffect(() => {
    if (!pendingFilename) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      refreshSources().then((updated) => {
        if (updated.some((item) => item.name === pendingFilename)) {
          setPendingFilename(null);
          setUploadState("idle");
          setProgress(100);
          setMessage(`${pendingFilename} is published and ready for TV refresh.`);
        }
      }).catch(() => undefined);
      if (attempts >= 86) {
        window.clearInterval(timer);
        setPendingFilename(null);
        setUploadState("error");
        setMessage("GitHub did not publish the source within five minutes. Check the Publish TV source workflow, then try again.");
      }
    }, 3500);
    return () => window.clearInterval(timer);
  }, [pendingFilename, refreshSources]);

  async function addSource(file: File | null) {
    if (!file) return;
    try {
      const filename = assertSourceFilename(file.name);
      assertSourceSize(file.size);
      if (sources.some((item) => item.name === filename)) {
        throw new SourceValidationError("A source with that filename already exists.");
      }

      const requestId = crypto.randomUUID().replace(/-/g, "");
      setUploadState("uploading");
      setProgress(0);
      setMessage(`Uploading ${filename}…`);
      await upload(`pending/${requestId}/${filename}`, file, {
        access: "public",
        handleUploadUrl: "/api/uploads",
        clientPayload: JSON.stringify({ filename, requestId }),
        multipart: file.size > 4 * 1024 * 1024,
        onUploadProgress: ({ percentage }) => setProgress(Math.round(percentage)),
      });
      setPendingFilename(filename);
      setUploadState("queued");
      setMessage(`${filename} uploaded. Waiting for GitHub to publish it…`);
    } catch (error) {
      setUploadState("error");
      setMessage(error instanceof Error ? error.message : "The upload could not be started.");
    }
  }

  async function removeSource(source: SourceRecord) {
    if (!window.confirm(`Remove ${source.name} from the published TV library?`)) return;
    try {
      setDeleting(source.name);
      setMessage("");
      const response = await fetch("/api/sources", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: source.name, sha: source.sha }),
      });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "The source could not be removed.");
      await refreshSources();
      setMessage(`${source.name} was removed from the current published library.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The source could not be removed.");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section className="source-manager">
      <section className="summary-card">
        <div><strong>{sources.length}</strong><span>published sources</span></div>
        <div><strong>{formatBytes(totalSize)}</strong><span>current library size</span></div>
        <div><strong>{formatBytes(SOURCE_MAX_BYTES)}</strong><span>maximum per file</span></div>
      </section>

      <section className="upload-card">
        <div>
          <h2>Add a source</h2>
          <p>Files are uploaded securely to temporary storage, then committed into <code>sources/</code>. Existing names are protected from overwrite.</p>
        </div>
        <label className="file-picker">
          <span>Choose media or document</span>
          <input
            type="file"
            accept=".mp4,.mov,.m4v,.webm,.mp3,.wav,.ogg,.jpg,.jpeg,.png,.gif,.bmp,.webp,.pdf,.ppt,.pptx"
            disabled={uploadState === "uploading" || uploadState === "queued"}
            onChange={(event) => { void addSource(event.target.files?.[0] || null); event.currentTarget.value = ""; }}
          />
        </label>
        {uploadState === "uploading" || uploadState === "queued" ? <progress value={progress} max="100" /> : null}
        {message ? <p className={uploadState === "error" ? "form-error" : "status-message"}>{message}</p> : null}
      </section>

      <section className="table-card">
        <div className="table-heading"><h2>Published library</h2><button className="button secondary" type="button" onClick={() => void refreshSources()}>Refresh</button></div>
        {sources.length ? (
          <div className="source-table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Revision</th><th>Preview</th><th /></tr></thead>
            <tbody>{sources.map((source) => <tr key={source.sha}><td>{source.name}</td><td>{source.name.split(".").pop()?.toUpperCase()}</td><td>{formatBytes(source.size)}</td><td><code>{source.sha.slice(0, 10)}</code></td><td>{source.downloadUrl ? <a href={source.downloadUrl} target="_blank" rel="noreferrer">Open</a> : "—"}</td><td><button className="button danger" type="button" disabled={deleting === source.name} onClick={() => void removeSource(source)}>{deleting === source.name ? "Removing…" : "Delete"}</button></td></tr>)}</tbody>
          </table></div>
        ) : <p className="empty-state">No sources are published yet.</p>}
      </section>
    </section>
  );
}
