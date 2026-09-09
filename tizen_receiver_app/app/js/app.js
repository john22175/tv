const STORAGE_KEYS = {
  alias: "multihub.receiverAlias",
  localSourceOverrideRevision: "multihub.localSourceOverrideRevision",
};
const SOURCE_REPOSITORY = globalThis.MultiHubSourceRepository || {};
const GITHUB_OWNER = String(SOURCE_REPOSITORY.owner || "john22175");
const GITHUB_REPOSITORY = String(SOURCE_REPOSITORY.repository || "t-sources");
const GITHUB_BRANCH = String(SOURCE_REPOSITORY.branch || "main");
const GITHUB_COMMIT_URL = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPOSITORY}/commits/${GITHUB_BRANCH}`;
const GITHUB_ROOT_TREE_URL = (treeSha) => `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPOSITORY}/git/trees/${encodeURIComponent(treeSha)}`;
const GITHUB_SOURCES_TREE_URL = (treeSha) => `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPOSITORY}/git/trees/${encodeURIComponent(treeSha)}?recursive=1`;
const GITHUB_RAW_URL = (commitSha, path) => `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPOSITORY}/${encodeURIComponent(commitSha)}/${path.split("/").map((part) => encodeURIComponent(part)).join("/")}`;

const statusBadge = document.getElementById("statusBadge");
const aliasBadge = document.getElementById("aliasBadge");
const playerView = document.getElementById("playerView");
const viewport = document.getElementById("viewport");
const headline = document.getElementById("headline");
const note = document.getElementById("note");
const sourceMenu = document.getElementById("sourceMenu");
const sourceMenuItems = document.getElementById("sourceMenuItems");
const sourceMenuPosition = document.getElementById("sourceMenuPosition");
const refreshSourcesAction = document.getElementById("refreshSourcesAction");
const viewRefreshLogsAction = document.getElementById("viewRefreshLogsAction");
const refreshLogMenu = document.getElementById("refreshLogMenu");
const refreshLogItems = document.getElementById("refreshLogItems");
const refreshLogPosition = document.getElementById("refreshLogPosition");

const OFFLINE_LIBRARY_DB = "multihub.offlineLibrary";
const OFFLINE_LIBRARY_DB_VERSION = 1;
const MAX_GITHUB_REFRESH_LOGS = 30;
// Use the public Documents virtual root: it is designed for large downloads,
// produces playable file URIs, and survives receiver package updates.
const TIZEN_OFFLINE_LIBRARY_ROOT = "documents";
const TIZEN_OFFLINE_LIBRARY_DIRECTORY = "multihub-offline-library";

let currentConfig = null;
let currentRenderKey = null;
let playbackProbeTimer = null;
let currentReceiverState = null;
let localPlaybackOverride = null;
let offlineLibrary = {
  revision: "",
  entries: [],
  folders: [],
  selectedId: "",
  storedBytes: 0,
  lastRequestId: "",
  failedRequestId: "",
};
let offlineLibraryReady = false;
let offlineLibrarySyncPromise = null;
let offlineActive = false;
let sourceMenuOpen = false;
let sourceMenuIndex = 0;
let sourceMenuFocus = "sources";
let sourceMenuActionIndex = 0;
let sourceMenuFolder = "";
let refreshLogMenuOpen = false;
let githubRefreshLogs = [];
let frontendStageRevision = "";
let frontendStageActive = false;
let receiverStagePollTimer = null;
let slideShowTimer = null;
let activePlaybackOffsetSeconds = 0;
let activePlaybackOffsetApplied = false;
let currentPlaybackToken = 0;
let activePlayback = {
  mode: null,
  mediaUrl: null,
  element: null,
};
let pictureInPictureElements = [];
const REMOTE_KEYS = [
  "MediaPlayPause",
  "MediaPlay",
  "MediaPause",
  "MediaStop",
  "MediaRewind",
  "MediaFastForward",
];
const REMOTE_KEY_NAMES = {
  toggle: new Set(["Enter", "MediaPlayPause", "XF86AudioPlay", "XF86AudioPause"]),
  play: new Set(["MediaPlay", "XF86AudioPlay"]),
  pause: new Set(["MediaPause", "XF86AudioPause"]),
  stop: new Set(["MediaStop", "XF86AudioStop"]),
  rewind: new Set(["MediaRewind", "XF86AudioRewind", "ArrowLeft"]),
  fastForward: new Set(["MediaFastForward", "XF86AudioForward", "ArrowRight"]),
};

function setImmersivePlayback(active) {
  document.body.classList.toggle("media-active", active);
}

function normalizeAlias(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(0, 8);
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed."));
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed."));
    transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction was aborted."));
  });
}

function openOfflineLibraryDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("Persistent TV storage is unavailable."));
      return;
    }
    const request = window.indexedDB.open(OFFLINE_LIBRARY_DB, OFFLINE_LIBRARY_DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("meta")) {
        database.createObjectStore("meta");
      }
      if (!database.objectStoreNames.contains("blobs")) {
        database.createObjectStore("blobs", { keyPath: "content_hash" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Could not open persistent TV storage."));
  });
}

async function readOfflineLibraryMeta() {
  const database = await openOfflineLibraryDb();
  try {
    const transaction = database.transaction("meta", "readonly");
    const done = transactionDone(transaction);
    const value = await requestResult(transaction.objectStore("meta").get("active"));
    await done;
    return value || null;
  } finally {
    database.close();
  }
}

async function writeOfflineLibraryMeta(value) {
  const database = await openOfflineLibraryDb();
  try {
    const transaction = database.transaction("meta", "readwrite");
    transaction.objectStore("meta").put(value, "active");
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

async function getOfflineBlob(contentHash) {
  const database = await openOfflineLibraryDb();
  try {
    const transaction = database.transaction("blobs", "readonly");
    const done = transactionDone(transaction);
    const record = await requestResult(transaction.objectStore("blobs").get(contentHash));
    await done;
    return record && record.blob instanceof Blob ? record.blob : null;
  } finally {
    database.close();
  }
}

async function putOfflineBlob(contentHash, blob) {
  const database = await openOfflineLibraryDb();
  try {
    const transaction = database.transaction("blobs", "readwrite");
    transaction.objectStore("blobs").put({ content_hash: contentHash, blob, size: blob.size });
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}

async function removeUnusedOfflineBlobs(entries) {
  const retained = new Set(entries.map((entry) => entry.content_hash));
  const database = await openOfflineLibraryDb();
  try {
    const transaction = database.transaction("blobs", "readwrite");
    const store = transaction.objectStore("blobs");
    const done = transactionDone(transaction);
    const keys = await requestResult(store.getAllKeys());
    for (const key of keys) {
      if (!retained.has(String(key))) {
        store.delete(key);
      }
    }
    await done;
  } finally {
    database.close();
  }
}

function supportsTizenOfflineFiles() {
  return typeof tizen !== "undefined"
    && Boolean(tizen && tizen.filesystem && tizen.download && tizen.DownloadRequest);
}

function tizenOfflineDirectoryPath() {
  return `${TIZEN_OFFLINE_LIBRARY_ROOT}/${TIZEN_OFFLINE_LIBRARY_DIRECTORY}`;
}

function tizenOfflineFileName(entry) {
  const name = String(entry.name || "");
  const extension = name.includes(".")
    ? name.split(".").pop().replace(/[^a-z0-9]/gi, "").slice(0, 12).toLowerCase()
    : "bin";
  return `${entry.content_hash}.${extension || "bin"}`;
}

function resolveTizenFile(path, mode = "r") {
  return new Promise((resolve, reject) => {
    try {
      tizen.filesystem.resolve(path, resolve, reject, mode);
    } catch (error) {
      reject(error);
    }
  });
}

async function ensureTizenOfflineDirectory() {
  const root = await resolveTizenFile(TIZEN_OFFLINE_LIBRARY_ROOT, "rw");
  try {
    const directory = root.resolve(TIZEN_OFFLINE_LIBRARY_DIRECTORY);
    if (!directory.isDirectory) {
      throw new Error("The receiver offline-storage path is not a directory.");
    }
    return directory;
  } catch (error) {
    return root.createDirectory(TIZEN_OFFLINE_LIBRARY_DIRECTORY);
  }
}

async function getStoredTizenOfflineFile(entry) {
  const path = String(entry.offline_path || "");
  if (!path) {
    return null;
  }
  try {
    const file = await resolveTizenFile(path, "r");
    if (!file.isFile || Number(file.fileSize) !== Number(entry.size)) {
      return null;
    }
    return {
      path,
      uri: file.toURI(),
    };
  } catch (error) {
    return null;
  }
}

async function deleteTizenOfflinePath(path) {
  if (!path) {
    return;
  }
  try {
    const file = await resolveTizenFile(path, "rw");
    if (!file.isFile || !file.parent) {
      return;
    }
    await new Promise((resolve, reject) => file.parent.deleteFile(file.fullPath, resolve, reject));
  } catch (error) {
    // A missing partial download is already the desired state.
  }
}

async function downloadTizenOfflineFile(entry) {
  await ensureTizenOfflineDirectory();
  const requestedPath = `${tizenOfflineDirectoryPath()}/${tizenOfflineFileName(entry)}`;
  const existing = await getStoredTizenOfflineFile({ ...entry, offline_path: requestedPath });
  if (existing) {
    return { ...entry, offline_path: existing.path, offline_storage: "filesystem" };
  }
  await deleteTizenOfflinePath(requestedPath);

  return new Promise((resolve, reject) => {
    const listener = {
      onprogress() {},
      onpaused() {},
      oncanceled() {
        reject(new Error(`${entry.name || "Source"} download was canceled.`));
      },
      oncompleted(_downloadId, completedPath) {
        Promise.resolve().then(async () => {
          // The requested filename is unique (the content hash), so resolve
          // the virtual path we own instead of retaining a device-specific
          // physical path from the download callback.
          const file = await resolveTizenFile(requestedPath, "r");
          if (!file.isFile || Number(file.fileSize) !== Number(entry.size)) {
            throw new Error(`${entry.name || "Source"} downloaded with the wrong size.`);
          }
          resolve({ ...entry, offline_path: requestedPath, offline_storage: "filesystem" });
        }).catch(reject);
      },
      onfailed(_downloadId, error) {
        reject(new Error(`${entry.name || "Source"} download failed: ${error && (error.message || error.name) || "unknown error"}.`));
      },
    };
    try {
      const request = new tizen.DownloadRequest(
        entry.media_url,
        tizenOfflineDirectoryPath(),
        tizenOfflineFileName(entry),
        "ALL",
      );
      const downloadId = tizen.download.start(request, listener);
      if (Number(downloadId) < 0) {
        reject(new Error(`${entry.name || "Source"} download could not be started.`));
      }
    } catch (error) {
      reject(error);
    }
  });
}

async function removeUnusedTizenOfflineFiles(entries) {
  const retainedNames = new Set(entries.map((entry) => tizenOfflineFileName(entry)));
  const directory = await ensureTizenOfflineDirectory();
  const files = await new Promise((resolve, reject) => directory.listFiles(resolve, reject));
  for (const file of files) {
    if (file.isFile && !retainedNames.has(String(file.name || ""))) {
      await new Promise((resolve, reject) => directory.deleteFile(file.fullPath, resolve, reject));
    }
  }
}

async function offlineLibraryFilesPresent() {
  for (const entry of offlineLibrary.entries) {
    if (entry.remote_only) {
      continue;
    }
    if (entry.offline_storage === "filesystem") {
      if (!await getStoredTizenOfflineFile(entry)) {
        return false;
      }
      continue;
    }
    const blob = await getOfflineBlob(entry.content_hash);
    if (!blob || Number(blob.size) !== Number(entry.size)) {
      return false;
    }
  }
  return true;
}

function uniqueLibraryBytes(entries) {
  const seen = new Set();
  return entries.reduce((total, entry) => {
    if (entry.remote_only) {
      return total;
    }
    if (seen.has(entry.content_hash)) {
      return total;
    }
    seen.add(entry.content_hash);
    return total + Math.max(0, Number(entry.size || 0));
  }, 0);
}

async function persistOfflineLibraryMeta() {
  await writeOfflineLibraryMeta({
    revision: offlineLibrary.revision,
    entries: offlineLibrary.entries,
    folders: offlineLibrary.folders,
    selected_id: offlineLibrary.selectedId,
    stored_bytes: offlineLibrary.storedBytes,
    last_request_id: offlineLibrary.lastRequestId,
    failed_request_id: offlineLibrary.failedRequestId,
    github_refresh_logs: githubRefreshLogs,
  });
}

async function loadOfflineLibrary() {
  try {
    const stored = await readOfflineLibraryMeta();
    if (stored && Array.isArray(stored.entries)) {
      offlineLibrary = {
        revision: String(stored.revision || ""),
        entries: stored.entries
          .filter((entry) => entry && entry.id && entry.content_hash)
          .map((entry) => ({ ...entry, playable: isPlayableMimeType(entry.mime_type) })),
        folders: Array.isArray(stored.folders)
          ? stored.folders.filter((folder) => typeof folder === "string" && folder && !folder.split("/").some((part) => part.startsWith(".")))
          : [],
        selectedId: String(stored.selected_id || ""),
        storedBytes: Math.max(0, Number(stored.stored_bytes || 0)),
        lastRequestId: String(stored.last_request_id || ""),
        failedRequestId: String(stored.failed_request_id || ""),
      };
      githubRefreshLogs = Array.isArray(stored.github_refresh_logs)
        ? stored.github_refresh_logs
          .filter((entry) => entry && entry.message)
          .slice(0, MAX_GITHUB_REFRESH_LOGS)
          .map((entry) => ({
            at: String(entry.at || ""),
            type: String(entry.type || "info"),
            message: String(entry.message || ""),
          }))
        : [];
    }
  } catch (error) {
    setStatus("Offline storage unavailable", "error");
    console.error("Could not load offline library", error);
  } finally {
    offlineLibraryReady = true;
  }
}

function offlineLibraryEntryById(itemId, contentHash = "") {
  return offlineLibrary.entries.find(
    (entry) => entry.id === itemId && (!contentHash || entry.content_hash === contentHash),
  ) || null;
}

function mimeTypeForName(name) {
  const normalizedName = String(name || "").toLowerCase();
  if (normalizedName.endsWith(".pip.json")) {
    return "application/vnd.multihub.picture-in-picture+json";
  }
  const suffix = normalizedName.split(".").pop() || "";
  const types = {
    mp4: "video/mp4",
    mov: "video/quicktime",
    m4v: "video/x-m4v",
    webm: "video/webm",
    mp3: "audio/mpeg",
    wav: "audio/wav",
    ogg: "audio/ogg",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    gif: "image/gif",
    bmp: "image/bmp",
    webp: "image/webp",
    pdf: "application/pdf",
    ppt: "application/vnd.ms-powerpoint",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  };
  if (normalizedName.endsWith(".slides.json")) {
    return "application/vnd.multihub.slide-show+json";
  }
  return types[suffix] || "application/octet-stream";
}

function isPlayableMimeType(mimeType) {
  const value = String(mimeType || "").toLowerCase();
  return value === "application/vnd.multihub.picture-in-picture+json"
    || value === "application/vnd.multihub.slide-show+json"
    || value === "application/vnd.ms-powerpoint"
    || value === "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    || ["image/", "video/", "audio/"].some((prefix) => value.startsWith(prefix));
}

function isPictureInPictureRecipePath(path) {
  return String(path || "").toLowerCase().endsWith(".pip.json");
}

function isSlideShowRecipePath(path) {
  return String(path || "").toLowerCase().endsWith(".slides.json");
}

function isPresentationPath(path) {
  return /\.(ppt|pptx)$/i.test(String(path || ""));
}

function slideShowRecipePathForPresentation(path) {
  const value = String(path || "").trim().replace(/^\/+|\/+$/g, "");
  return isPresentationPath(value) ? value.replace(/\.(ppt|pptx)$/i, ".slides.json") : "";
}

function loadLocalSourceOverrideRevision() {
  try {
    return String(localStorage.getItem(STORAGE_KEYS.localSourceOverrideRevision) || "");
  } catch (error) {
    return "";
  }
}

function saveLocalSourceOverrideRevision(revision) {
  try {
    if (revision) {
      localStorage.setItem(STORAGE_KEYS.localSourceOverrideRevision, String(revision));
    } else {
      localStorage.removeItem(STORAGE_KEYS.localSourceOverrideRevision);
    }
  } catch (error) {}
}

async function fetchGitHubLibraryManifest() {
  const commitResponse = await fetch(GITHUB_COMMIT_URL, { cache: "no-store" });
  if (!commitResponse.ok) {
    throw new Error(`GitHub commit lookup returned HTTP ${commitResponse.status}.`);
  }
  const commit = await commitResponse.json();
  const commitSha = String(commit && commit.sha || "").trim();
  if (!commitSha) {
    throw new Error("GitHub did not provide a commit revision.");
  }

  const rootTreeSha = String(commit && commit.commit && commit.commit.tree && commit.commit.tree.sha || "").trim();
  if (!rootTreeSha) {
    throw new Error("GitHub did not provide a root tree revision.");
  }
  // Find sources/ in the non-recursive root tree, then query that subtree.
  // This gives folder support without ever scanning receiver or repo files.
  const rootResponse = await fetch(GITHUB_ROOT_TREE_URL(rootTreeSha), { cache: "no-store" });
  if (!rootResponse.ok) {
    throw new Error(`GitHub source lookup returned HTTP ${rootResponse.status}.`);
  }
  const rootPayload = await rootResponse.json();
  const sourcesNode = (Array.isArray(rootPayload && rootPayload.tree) ? rootPayload.tree : [])
    .find((node) => node && node.type === "tree" && node.path === "sources");
  if (!sourcesNode || !sourcesNode.sha) {
    return {
      receiver_id: "github-public-library",
      revision: `github:${commitSha}`,
      request_id: `github:${commitSha}`,
      entries: [],
      folders: [],
    };
  }
  const sourcesResponse = await fetch(GITHUB_SOURCES_TREE_URL(String(sourcesNode.sha)), { cache: "no-store" });
  if (!sourcesResponse.ok) {
    throw new Error(`GitHub source lookup returned HTTP ${sourcesResponse.status}.`);
  }
  const sourcePayload = await sourcesResponse.json();
  if (sourcePayload && sourcePayload.truncated) {
    throw new Error("GitHub sources tree is too large to refresh safely.");
  }

  const entries = (Array.isArray(sourcePayload && sourcePayload.tree) ? sourcePayload.tree : [])
    .filter((node) => node && node.type === "blob" && globalThis.MultiHubSourceLibrary.isGitHubSourcePath(`sources/${String(node.path || "")}`))
    .filter((node) => !isSlideShowRecipePath(String(node.path || "")))
    .map((node) => {
      const relativePath = String(node.path || "");
      const path = `sources/${relativePath}`;
      const size = Number(node.size);
      const mimeType = mimeTypeForName(relativePath);
      // Recipes are small public control documents. Render them directly from
      // GitHub instead of relying on the TV download service to preserve a
      // custom JSON MIME type in its offline cache.
      const remoteOnly = isPictureInPictureRecipePath(relativePath)
        || isPresentationPath(relativePath)
        || isSlideShowRecipePath(relativePath);
      return {
        id: `github:${relativePath}`,
        name: relativePath,
        mime_type: mimeType,
        size: Number.isFinite(size) && size >= 0 ? size : 0,
        // Git blob SHA values are filesystem-safe and let the TV reuse an
        // unchanged downloaded file across repository revisions.
        content_hash: String(node.sha || relativePath).replace(/[^a-zA-Z0-9_-]/g, "_"),
        playable: isPlayableMimeType(mimeType),
        // The PowerPoint archive and its generated manifest stay online. The
        // receiver streams the rendered PNG slides when selected instead of
        // spending TV storage on the original Office document.
        remote_only: remoteOnly,
        media_url: GITHUB_RAW_URL(commitSha, path),
      };
    })
    .sort((left, right) => String(left.id).localeCompare(String(right.id)));
  const folders = (Array.isArray(sourcePayload && sourcePayload.tree) ? sourcePayload.tree : [])
    .filter((node) => node && node.type === "tree" && globalThis.MultiHubSourceLibrary.isGitHubSourcePath(`sources/${String(node.path || "")}`))
    .map((node) => String(node.path || ""))
    .filter(Boolean)
    .sort();

  return {
    receiver_id: "github-public-library",
    revision: `github:${commitSha}`,
    request_id: `github:${commitSha}`,
    entries,
    folders,
  };
}

async function ensureOfflineLibraryCapacity(entries) {
  if (!navigator.storage || typeof navigator.storage.estimate !== "function") {
    return;
  }
  const cachedHashes = new Set(offlineLibrary.entries.map((entry) => entry.content_hash));
  const requiredBytes = uniqueLibraryBytes(entries.filter((entry) => !cachedHashes.has(entry.content_hash)));
  const estimate = await navigator.storage.estimate();
  const availableBytes = Number(estimate.quota || 0) - Number(estimate.usage || 0);
  if (Number.isFinite(availableBytes) && availableBytes >= 0 && availableBytes < requiredBytes) {
    throw new Error(`Not enough TV storage for ${Math.ceil(requiredBytes / (1024 * 1024))} MB of new sources.`);
  }
}

async function applyOfflineLibraryManifest(manifest) {
  const entries = Array.isArray(manifest.entries) ? manifest.entries.filter(
    (entry) => entry && entry.id && entry.content_hash && entry.media_url && Number(entry.size) >= 0,
  ) : [];
  const useTizenFileStorage = supportsTizenOfflineFiles();
  if (!useTizenFileStorage) {
    await ensureOfflineLibraryCapacity(entries);
  }

  const storedByHash = new Map();
  const storedEntries = [];
  for (const entry of entries) {
    if (entry.remote_only) {
      storedByHash.set(entry.content_hash, entry);
      storedEntries.push(entry);
      continue;
    }
    let storedEntry = storedByHash.get(entry.content_hash);
    if (!storedEntry && useTizenFileStorage) {
      const priorEntry = offlineLibrary.entries.find((candidate) => candidate.content_hash === entry.content_hash);
      const existing = await getStoredTizenOfflineFile(priorEntry || entry);
      storedEntry = existing
        ? { ...entry, offline_path: existing.path, offline_storage: "filesystem" }
        : await downloadTizenOfflineFile(entry);
    }
    if (!storedEntry) {
      const existingBlob = await getOfflineBlob(entry.content_hash);
      if (existingBlob && Number(existingBlob.size) === Number(entry.size)) {
        storedEntry = entry;
      } else {
        const response = await fetch(entry.media_url, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`${entry.name || "Source"} download returned HTTP ${response.status}.`);
        }
        const blob = await response.blob();
        if (blob.size !== Number(entry.size)) {
          throw new Error(`${entry.name || "Source"} downloaded with the wrong size.`);
        }
        await putOfflineBlob(entry.content_hash, blob);
        storedEntry = entry;
      }
    }
    storedByHash.set(entry.content_hash, storedEntry);
    storedEntries.push(storedEntry);
  }

  const previousSelection = offlineLibrary.selectedId;
  offlineLibrary = {
    revision: String(manifest.revision || ""),
    entries: storedEntries,
    folders: Array.isArray(manifest.folders) ? manifest.folders.filter((folder) => typeof folder === "string" && folder) : [],
    selectedId: storedEntries.some((entry) => entry.id === previousSelection) ? previousSelection : "",
    storedBytes: uniqueLibraryBytes(storedEntries),
    lastRequestId: String(manifest.request_id || ""),
    failedRequestId: "",
  };
  await persistOfflineLibraryMeta();
  if (useTizenFileStorage) {
    await removeUnusedTizenOfflineFiles(storedEntries);
  } else {
    await removeUnusedOfflineBlobs(storedEntries);
  }
}

async function applyLibrarySync(manifest, { retryFailedRevision = false } = {}) {
  const requestId = String(manifest.request_id || "");
  const revision = String(manifest.revision || "");
  if (!requestId) {
    return false;
  }
  if (requestId === offlineLibrary.failedRequestId && !retryFailedRevision) {
    return false;
  }
  const savedFilesPresent = await offlineLibraryFilesPresent();
  if (requestId === offlineLibrary.lastRequestId && revision === offlineLibrary.revision && savedFilesPresent) {
    return false;
  }
  if (revision === offlineLibrary.revision && savedFilesPresent) {
    offlineLibrary.lastRequestId = requestId;
    offlineLibrary.failedRequestId = "";
    await persistOfflineLibraryMeta();
    return false;
  }
  try {
    await applyOfflineLibraryManifest(manifest);
    return true;
  } catch (error) {
    offlineLibrary.lastRequestId = requestId;
    offlineLibrary.failedRequestId = requestId;
    await persistOfflineLibraryMeta();
    throw error;
  }
}

function formatGitHubRefreshLogTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Now";
  }
  const pad = (number) => String(number).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function renderRefreshLogMenu() {
  refreshLogItems.innerHTML = "";
  const visibleEntries = githubRefreshLogs.slice(0, 10);
  refreshLogPosition.textContent = visibleEntries.length ? `${visibleEntries.length} recent` : "No refreshes yet";
  if (!visibleEntries.length) {
    const empty = document.createElement("div");
    empty.className = "refresh-log-item";
    empty.textContent = "The receiver will write GitHub refresh activity here.";
    refreshLogItems.appendChild(empty);
    return;
  }
  for (const entry of visibleEntries) {
    const item = document.createElement("div");
    item.className = `refresh-log-item${entry.type === "error" ? " error" : ""}`;
    const time = document.createElement("span");
    time.className = "refresh-log-time";
    time.textContent = formatGitHubRefreshLogTime(entry.at);
    const message = document.createElement("span");
    message.className = "refresh-log-message";
    message.textContent = entry.message;
    item.append(time, message);
    refreshLogItems.appendChild(item);
  }
}

function recordGitHubRefreshLog(message, type = "info") {
  const detail = String(message || "GitHub refresh event.");
  githubRefreshLogs = [{
    at: new Date().toISOString(),
    type: type === "error" ? "error" : "info",
    message: detail,
  }, ...githubRefreshLogs].slice(0, MAX_GITHUB_REFRESH_LOGS);
  renderRefreshLogMenu();
  if (offlineLibraryReady) {
    persistOfflineLibraryMeta().catch((error) => console.warn("Could not save GitHub refresh logs", error));
  }
}

async function runLibrarySync(loadManifest, options = {}) {
  if (offlineLibrarySyncPromise) {
    return offlineLibrarySyncPromise;
  }
  offlineLibrarySyncPromise = (async () => applyLibrarySync(await loadManifest(), options))();
  try {
    return await offlineLibrarySyncPromise;
  } finally {
    offlineLibrarySyncPromise = null;
  }
}

async function syncGitHubOfflineLibrary({ retryFailedRevision = false } = {}) {
  return runLibrarySync(fetchGitHubLibraryManifest, { retryFailedRevision });
}

async function requestGitHubSourceRefresh(trigger = "startup") {
  if (offlineLibrarySyncPromise) {
    recordGitHubRefreshLog("Refresh requested while another source refresh is still running.");
    return { changed: false, alreadyRunning: true };
  }

  const isManual = trigger === "manual";
  recordGitHubRefreshLog(isManual ? "Manual refresh started: checking GitHub main." : "Startup refresh started: checking GitHub main.");
  try {
    const changed = await syncGitHubOfflineLibrary({ retryFailedRevision: isManual });
    const revision = String(offlineLibrary.revision || "").replace(/^github:/, "").slice(0, 12);
    const summary = `${offlineLibrary.entries.length} source(s)${revision ? ` at ${revision}` : ""}`;
    recordGitHubRefreshLog(changed ? `Refresh completed: saved ${summary}.` : `Refresh completed: ${summary} already current.`);
    return { changed, alreadyRunning: false };
  } catch (error) {
    const detail = String(error && error.message || error || "Unknown GitHub refresh error.");
    recordGitHubRefreshLog(`Refresh failed: ${detail}`, "error");
    throw error;
  }
}

async function renderOfflineLibraryEntry(entry, { persistSelection = true, dashboardStage = false, manualSelection = false } = {}) {
  if (!entry) {
    return false;
  }
  // A stage command wins over any in-flight offline-library rendering. This
  // matters during startup, when source hydration can complete after a
  // picture-in-picture command has already painted the screen.
  if (frontendStageActive && !dashboardStage && !manualSelection) {
    return false;
  }
  offlineActive = true;
  if (persistSelection) {
    offlineLibrary.selectedId = entry.id;
    try {
      await persistOfflineLibraryMeta();
    } catch (error) {
      console.warn("Could not save the offline selection", error);
    }
  }
  if (isPictureInPictureRecipePath(entry.name)) {
    try {
      await renderPictureInPictureRecipe({
        revision: `library:${String(entry.content_hash || entry.id || entry.name)}`,
        sourcePath: entry.name,
      });
      setStatus("Picture in Picture");
      showRemoteFeedback(`${entry.name} selected from this TV.`);
      return true;
    } catch (error) {
      renderCard("Picture in Picture Error", String(error && error.message || error || "The saved recipe could not be loaded."));
      setStatus("Picture in Picture Error", "error");
      return false;
    }
  }
  if (isPresentationPath(entry.name)) {
    try {
      await renderSlideShowRecipe({
        revision: `presentation:${String(entry.content_hash || entry.id || entry.name)}`,
        sourcePath: slideShowRecipePathForPresentation(entry.name),
        presentationSourcePath: entry.name,
      });
      return true;
    } catch (error) {
      renderCard("PowerPoint Not Ready", String(error && error.message || error || "The PowerPoint could not be rendered."));
      setStatus("PowerPoint Not Ready", "error");
      return false;
    }
  }
  if (!entry.playable) {
    currentReceiverState = {
      source_name: entry.name,
      mime_type: entry.mime_type || "application/octet-stream",
      media_url: null,
      note: "This saved source cannot be displayed directly on the TV.",
      playback_state: "idle",
    };
    currentRenderKey = `offline-unsupported:${entry.id}:${entry.content_hash}`;
    stopActivePlayback();
    headline.textContent = entry.name;
    note.textContent = currentReceiverState.note;
    renderCard("Unsupported Source", currentReceiverState.note);
    setStatus("Saved Source");
    return true;
  }
  const storedFile = entry.offline_storage === "filesystem"
    ? await getStoredTizenOfflineFile(entry)
    : null;
  const blob = storedFile ? null : await getOfflineBlob(entry.content_hash);
  if (frontendStageActive && !dashboardStage && !manualSelection) {
    return false;
  }
  if (!storedFile && !blob) {
    renderCard("Saved Source Missing", `${entry.name} is not available in this TV's offline library.`);
    setStatus("Offline Storage Error", "error");
    return false;
  }
  const mediaUrl = storedFile ? storedFile.uri : URL.createObjectURL(blob);
  renderState({
    receiver_alias: currentConfig ? currentConfig.alias : "",
    source_name: entry.name,
    mime_type: entry.mime_type,
    media_url: mediaUrl,
    note: "Playing from this TV's saved source library.",
    playback_state: "playing",
    start_position_seconds: 0,
    playback_token: 0,
    library_item_id: entry.id,
    library_content_hash: entry.content_hash,
    offline_local: true,
    offline_file: Boolean(storedFile),
  }, { offline: true, dashboardStage: dashboardStage || manualSelection });
  setStatus("Offline · Saved");
  return true;
}

async function renderStoredOfflineSelection() {
  if (!offlineLibraryReady || !offlineLibrary.selectedId) {
    return false;
  }
  return renderOfflineLibraryEntry(offlineLibraryEntryById(offlineLibrary.selectedId), { persistSelection: false });
}

function closeSourceMenu() {
  refreshLogMenuOpen = false;
  sourceMenuOpen = false;
  sourceMenu.classList.add("hidden");
  sourceMenu.setAttribute("aria-hidden", "true");
  refreshLogMenu.classList.add("hidden");
  refreshLogMenu.setAttribute("aria-hidden", "true");
  claimRemoteFocus();
}

function sourceMenuEntries() {
  const folderPrefix = sourceMenuFolder ? `${sourceMenuFolder}/` : "";
  const folders = new Map();
  const files = [];
  // Git preserves explicitly-created empty folders with a hidden marker.
  // Keep those folders visible even before a source has been placed in them.
  for (const knownFolder of offlineLibrary.folders) {
    const relativePath = String(knownFolder || "");
    if (!relativePath.startsWith(folderPrefix)) {
      continue;
    }
    const remainder = relativePath.slice(folderPrefix.length);
    const divider = remainder.indexOf("/");
    const name = divider >= 0 ? remainder.slice(0, divider) : remainder;
    const path = `${folderPrefix}${name}`;
    if (name && !folders.has(path)) {
      folders.set(path, { kind: "folder", id: `folder:${path}`, name, path });
    }
  }
  for (const entry of offlineLibrary.entries) {
    const relativePath = String(entry.name || "");
    if (!relativePath.startsWith(folderPrefix)) {
      continue;
    }
    const remainder = relativePath.slice(folderPrefix.length);
    const divider = remainder.indexOf("/");
    if (divider >= 0) {
      const name = remainder.slice(0, divider);
      const path = `${folderPrefix}${name}`;
      if (name && !folders.has(path)) {
        folders.set(path, { kind: "folder", id: `folder:${path}`, name, path });
      }
    } else if (remainder) {
      files.push({ kind: "file", ...entry, display_name: remainder });
    }
  }
  return [...folders.values()].sort((left, right) => left.name.localeCompare(right.name))
    .concat(files.sort((left, right) => String(left.display_name).localeCompare(String(right.display_name))));
}

function sourceMenuParent() {
  if (!sourceMenuFolder) {
    return "";
  }
  const index = sourceMenuFolder.lastIndexOf("/");
  return index < 0 ? "" : sourceMenuFolder.slice(0, index);
}

function renderSourceMenu() {
  const entries = sourceMenuEntries();
  const actionFocused = sourceMenuFocus === "actions";
  refreshSourcesAction.classList.toggle("selected", actionFocused && sourceMenuActionIndex === 0);
  refreshSourcesAction.classList.toggle("refreshing", Boolean(offlineLibrarySyncPromise));
  viewRefreshLogsAction.classList.toggle("selected", actionFocused && sourceMenuActionIndex === 1);
  sourceMenuItems.innerHTML = "";
  if (!entries.length) {
    sourceMenuPosition.textContent = "No saved sources";
    return;
  }
  if (sourceMenuIndex >= entries.length) {
    sourceMenuIndex = Math.max(0, entries.length - 1);
  }
  const visible = Math.min(5, entries.length);
  const start = entries.length <= visible ? 0 : sourceMenuIndex - Math.floor(visible / 2);
  for (let offset = 0; offset < visible; offset += 1) {
    const index = (start + offset + entries.length) % entries.length;
    const entry = entries[index];
    const item = document.createElement("div");
    item.className = `source-menu-item${!actionFocused && index === sourceMenuIndex ? " selected" : ""}${entry.kind === "folder" || entry.playable ? "" : " unsupported"}`;
    const name = document.createElement("div");
    name.className = "source-menu-item-name";
    name.textContent = entry.kind === "folder"
      ? `📁 ${entry.name}`
      : (isPictureInPictureRecipePath(entry.name) ? `Picture in Picture · ${entry.display_name.replace(/\.pip\.json$/i, "")}` : entry.display_name);
    const kind = document.createElement("div");
    kind.className = "source-menu-item-kind";
    kind.textContent = entry.kind === "folder"
      ? "Folder"
      : (isPictureInPictureRecipePath(entry.name) ? "Reusable configured composition" : (entry.playable ? String(entry.mime_type || "Saved source") : "Not playable on TV"));
    item.append(name, kind);
    sourceMenuItems.appendChild(item);
  }
  sourceMenuPosition.textContent = `${sourceMenuFolder || "sources"} · ${sourceMenuIndex + 1} / ${entries.length}`;
}

function openSourceMenu() {
  if (offlineLibrary.entries.length) {
    const selectedIndex = offlineLibrary.entries.findIndex((entry) => entry.id === offlineLibrary.selectedId);
    const selected = selectedIndex >= 0 ? offlineLibrary.entries[selectedIndex] : null;
    sourceMenuFolder = selected && String(selected.name || "").includes("/")
      ? String(selected.name).slice(0, String(selected.name).lastIndexOf("/"))
      : "";
    sourceMenuIndex = 0;
    sourceMenuFocus = "sources";
  } else {
    sourceMenuFocus = "actions";
    sourceMenuActionIndex = 0;
  }
  sourceMenuOpen = true;
  renderSourceMenu();
  sourceMenu.classList.remove("hidden");
  sourceMenu.setAttribute("aria-hidden", "false");
  claimRemoteFocus();
}

function moveSourceMenu(delta) {
  const entries = sourceMenuEntries();
  if (!entries.length) {
    return;
  }
  sourceMenuIndex = (sourceMenuIndex + delta + entries.length) % entries.length;
  renderSourceMenu();
}

async function chooseSourceMenuItem() {
  const entry = sourceMenuEntries()[sourceMenuIndex];
  if (!entry) {
    return;
  }
  if (entry.kind === "folder") {
    sourceMenuFolder = entry.path;
    sourceMenuIndex = 0;
    sourceMenuFocus = "sources";
    renderSourceMenu();
    return;
  }
  closeSourceMenu();
  // Choosing a source with the TV remote is an explicit local decision. Keep
  // it in front of the already-seen dashboard stage until the dashboard sends
  // a different revision.
  if (frontendStageRevision) {
    saveLocalSourceOverrideRevision(frontendStageRevision);
  }
  await renderOfflineLibraryEntry(entry, { manualSelection: true });
}

function openRefreshLogMenu() {
  refreshLogMenuOpen = true;
  renderRefreshLogMenu();
  refreshLogMenu.classList.remove("hidden");
  refreshLogMenu.setAttribute("aria-hidden", "false");
  claimRemoteFocus();
}

function closeRefreshLogMenu() {
  refreshLogMenuOpen = false;
  refreshLogMenu.classList.add("hidden");
  refreshLogMenu.setAttribute("aria-hidden", "true");
  sourceMenuFocus = "actions";
  sourceMenuActionIndex = 1;
  renderSourceMenu();
  claimRemoteFocus();
}

async function manuallyRefreshGitHubSources() {
  setStatus("Refreshing GitHub Sources");
  renderSourceMenu();
  try {
    const result = await requestGitHubSourceRefresh("manual");
    if (result.alreadyRunning) {
      showRemoteFeedback("GitHub refresh is already running. See logs for progress.");
      return;
    }
    setStatus(result.changed ? "GitHub Sources Updated" : "GitHub Sources Current");
    showRemoteFeedback(result.changed
      ? `${offlineLibrary.entries.length} GitHub source(s) saved on this TV.`
      : "GitHub sources are already current.");
  } catch (error) {
    setStatus("GitHub Refresh Failed", "error");
    showRemoteFeedback("GitHub refresh failed. Open View Logs for details.");
  } finally {
    renderSourceMenu();
  }
}

function setStatus(text, type = "default") {
  statusBadge.textContent = text;
  statusBadge.classList.toggle("error", type === "error");
}

function setAlias(alias) {
  aliasBadge.textContent = alias ? `TV ${alias}` : "TV ?";
}

function showPlayer() {
  playerView.classList.remove("hidden");
  claimRemoteFocus();
}

function saveAlias(alias) {
  const normalized = normalizeAlias(alias);
  if (!normalized) {
    return;
  }
  try {
    localStorage.setItem(STORAGE_KEYS.alias, normalized);
  } catch (error) {}
}

function loadConfig() {
  const params = new URLSearchParams(window.location.search);
  const paramAlias = normalizeAlias(params.get("alias"));
  if (paramAlias) {
    saveAlias(paramAlias);
  }

  let storedAlias = "";
  try {
    storedAlias = normalizeAlias(localStorage.getItem(STORAGE_KEYS.alias));
  } catch (error) {}

  const alias = paramAlias || storedAlias || "";
  return { alias };
}

function receiverTargetId() {
  const packaged = globalThis.MultiHubReceiverTarget || {};
  const target = String(packaged.id || "").trim().toLowerCase();
  return /^tv-[1-6]$/.test(target) ? target : "";
}

function receiverStageUrl(target) {
  const control = globalThis.MultiHubReceiverControl || {};
  const baseUrl = String(control.baseUrl || "").replace(/\/+$/, "");
  return baseUrl ? `${baseUrl}/api/receiver/${encodeURIComponent(target)}` : "";
}

function pictureInPictureBackgroundRemoval(value) {
  const candidate = value && typeof value === "object" ? value : null;
  const color = String(candidate && candidate.color || "").trim();
  if (!/^#[0-9a-f]{6}$/i.test(color)) {
    return null;
  }
  const tolerance = Number(candidate && candidate.tolerance);
  return {
    color: color.toLowerCase(),
    tolerance: Number.isFinite(tolerance) ? Math.max(0, Math.min(128, Math.round(tolerance))) : 32,
  };
}

function colorKeyedPictureInPictureImage(source, className, removal) {
  // Older Samsung browser engines can composite a transparent canvas as an
  // opaque black layer over its siblings. Key the image with a temporary
  // canvas, then display the resulting alpha-preserving PNG in an <img>.
  const output = document.createElement("img");
  output.className = `${className} picture-in-picture-background-removed`;
  output.alt = String(source && source.sourceName || source && source.sourcePath || "Picture in picture");
  const sourceImage = new Image();
  sourceImage.crossOrigin = "anonymous";
  sourceImage.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context || !sourceImage.naturalWidth || !sourceImage.naturalHeight) return;
      canvas.width = sourceImage.naturalWidth;
      canvas.height = sourceImage.naturalHeight;
      context.drawImage(sourceImage, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
      const hex = removal.color.slice(1);
      const red = parseInt(hex.slice(0, 2), 16);
      const green = parseInt(hex.slice(2, 4), 16);
      const blue = parseInt(hex.slice(4, 6), 16);
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
      output.src = canvas.toDataURL("image/png");
    } catch (error) {
      console.warn("Could not remove the picture-in-picture background", error);
      output.src = String(source && source.mediaUrl || "");
    }
  };
  sourceImage.onerror = () => {
    output.src = String(source && source.mediaUrl || "");
  };
  sourceImage.src = String(source && source.mediaUrl || "");
  return output;
}

function pictureInPictureStageElement(source, className, removal = null) {
  const mimeType = String(source && source.mimeType || mimeTypeForName(source && source.sourcePath));
  const url = String(source && source.mediaUrl || "");
  if (!url) {
    return null;
  }
  if (removal && mimeType.startsWith("image/")) {
    return colorKeyedPictureInPictureImage(source, className, removal);
  }
  const element = document.createElement(mimeType.startsWith("image/") ? "img" : "video");
  element.className = className;
  element.src = url;
  element.alt = String(source && source.sourceName || source && source.sourcePath || "Dashboard source");
  if (element.tagName === "VIDEO") {
    element.autoplay = true;
    element.loop = true;
    element.muted = true;
    element.playsInline = true;
    element.preload = "auto";
    element.setAttribute("loop", "");
    element.setAttribute("playsinline", "");
    element.setAttribute("webkit-playsinline", "");
  }
  return element;
}

function pictureInPictureLayout(command) {
  const input = command && command.layout || {};
  const number = (value, fallback, minimum, maximum) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
  };
  const width = number(input.width, 0.3, 0.12, 0.88);
  const height = number(input.height, 0.3, 0.12, 0.88);
  return {
    x: number(input.x, 0.64, 0, 1 - width),
    y: number(input.y, 0.06, 0, 1 - height),
    width,
    height,
  };
}

function pictureInPictureSourceForPath(value) {
  const sourcePath = String(value || "").trim().replace(/^\/+|\/+$/g, "");
  if (!sourcePath || sourcePath.split("/").some((part) => !part || part === "." || part === ".." || part.startsWith("."))) {
    return null;
  }
  const mimeType = mimeTypeForName(sourcePath);
  if (!mimeType.startsWith("image/") && !mimeType.startsWith("video/")) {
    return null;
  }
  return {
    sourcePath,
    sourceName: sourcePath.split("/").pop() || sourcePath,
    mediaUrl: GITHUB_RAW_URL(GITHUB_BRANCH, `sources/${sourcePath}`),
    mimeType,
  };
}

function slideShowImageSourceForPath(value) {
  const sourcePath = String(value || "").trim().replace(/^\/+|\/+$/g, "");
  const parts = sourcePath.split("/");
  // Rendered slides are created only by the repository workflow beneath this
  // hidden folder. Do not let a manifest reference an arbitrary hidden file.
  if (!sourcePath.startsWith(".presentations/") || parts.some((part, index) => !part || part === "." || part === ".." || (index > 0 && part.startsWith(".")))) {
    return null;
  }
  const mimeType = mimeTypeForName(sourcePath);
  if (!mimeType.startsWith("image/")) {
    return null;
  }
  return {
    sourcePath,
    sourceName: sourcePath.split("/").pop() || sourcePath,
    mediaUrl: GITHUB_RAW_URL(GITHUB_BRANCH, `sources/${sourcePath}`),
    mimeType,
  };
}

function slideShowIntervalMs(value) {
  const seconds = Number(value);
  return Math.round(Math.min(60, Math.max(3, Number.isFinite(seconds) ? seconds : 10)) * 1000);
}

async function renderSlideShowRecipe(command) {
  const sourcePath = String(command && command.sourcePath || "");
  if (!isSlideShowRecipePath(sourcePath)) {
    throw new Error("The rendered slideshow is not ready yet. Refresh Sources after GitHub finishes converting the PowerPoint.");
  }
  const recipeUrl = GITHUB_RAW_URL(GITHUB_BRANCH, `sources/${sourcePath}`);
  const response = await fetch(recipeUrl, { cache: "no-store" });
  if (response.status === 404) {
    throw new Error("GitHub is still rendering this PowerPoint. Refresh Sources after conversion completes.");
  }
  if (!response.ok) {
    throw new Error(`PowerPoint slideshow returned HTTP ${response.status}.`);
  }
  const recipe = await response.json();
  const slides = Array.isArray(recipe && recipe.slides)
    ? recipe.slides.map(slideShowImageSourceForPath).filter(Boolean)
    : [];
  if (!recipe || recipe.kind !== "slide-show" || Number(recipe.version) !== 1 || !slides.length || slides.length > 300) {
    throw new Error("The rendered PowerPoint slideshow is invalid or has no image slides.");
  }

  const intervalMs = slideShowIntervalMs(recipe.intervalSeconds);
  const title = String(recipe.title || command && command.presentationSourcePath || sourcePath).trim();
  localPlaybackOverride = null;
  currentRenderKey = `slide-show:${String(command && command.revision || sourcePath)}`;
  currentPlaybackToken = 0;
  stopActivePlayback();
  offlineActive = false;
  currentReceiverState = {
    receiver_alias: currentConfig ? currentConfig.alias : "",
    source_name: title,
    mime_type: "application/vnd.multihub.slide-show+json",
    media_url: recipeUrl,
    playback_state: "playing",
    note: `${slides.length} slide${slides.length === 1 ? "" : "s"} · repeats every ${Math.round(intervalMs / 1000)} seconds.`,
  };
  headline.textContent = currentReceiverState.source_name;
  note.textContent = currentReceiverState.note;
  setImmersivePlayback(true);
  viewport.innerHTML = "";
  const stage = document.createElement("div");
  stage.className = "slide-show-stage";
  const image = document.createElement("img");
  image.className = "slide-show-image";
  image.alt = title;
  let index = 0;
  const showSlide = () => {
    const slide = slides[index];
    image.src = slide.mediaUrl;
    image.alt = `${title} — slide ${index + 1} of ${slides.length}`;
    headline.textContent = `${title} · Slide ${index + 1} of ${slides.length}`;
    index = (index + 1) % slides.length;
  };
  image.onerror = () => {
    note.textContent = "A rendered slide could not be loaded. Refresh Sources and try this PowerPoint again.";
  };
  stage.appendChild(image);
  viewport.appendChild(stage);
  showSlide();
  if (slides.length > 1) {
    slideShowTimer = setInterval(showSlide, intervalMs);
  }
  activePlayback = {
    mode: "slide-show",
    mediaUrl: recipeUrl,
    element: image,
  };
  applyPlaybackState(currentReceiverState);
  setStatus("PowerPoint Slide Show");
  showRemoteFeedback(`${title}: ${slides.length} slide${slides.length === 1 ? "" : "s"}, looping.`);
  claimRemoteFocus();
}

async function renderPictureInPictureRecipe(command) {
  const sourcePath = String(command && command.sourcePath || "");
  if (!isPictureInPictureRecipePath(sourcePath)) {
    throw new Error("The selected source is not a picture-in-picture recipe.");
  }
  // Resolve the recipe from the configured public source repository rather
  // than trusting a URL supplied by a command manifest.
  const recipeUrl = GITHUB_RAW_URL(GITHUB_BRANCH, `sources/${sourcePath}`);
  const response = await fetch(recipeUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Picture-in-picture recipe returned HTTP ${response.status}.`);
  }
  const recipe = await response.json();
  const base = pictureInPictureSourceForPath(recipe && recipe.baseSourcePath);
  const overlay = pictureInPictureSourceForPath(recipe && recipe.overlaySourcePath);
  if (!recipe || recipe.kind !== "picture-in-picture" || Number(recipe.version) !== 1 || !base || !overlay || base.sourcePath === overlay.sourcePath) {
    throw new Error("The picture-in-picture recipe is invalid or references unsupported sources.");
  }
  renderPictureInPictureStage({
    kind: "picture-in-picture",
    revision: String(command && command.revision || `recipe:${sourcePath}`),
    base,
    overlay,
    layout: recipe.layout,
    removeBackground: recipe.removeBackground,
  });
}

function renderPictureInPictureStage(command) {
  const base = command && command.base;
  const overlay = command && command.overlay;
  const baseElement = pictureInPictureStageElement(base, "picture-in-picture-base");
  const overlayElement = pictureInPictureStageElement(overlay, "picture-in-picture-overlay", pictureInPictureBackgroundRemoval(command && command.removeBackground));
  if (!baseElement || !overlayElement) {
    renderCard("Picture in Picture Error", "The dashboard command did not include two playable image or video sources.");
    setStatus("Picture in Picture Error", "error");
    return;
  }
  const layout = pictureInPictureLayout(command);
  localPlaybackOverride = null;
  currentRenderKey = `dashboard-pip:${String(command.revision || "")}`;
  currentPlaybackToken = 0;
  stopActivePlayback();
  offlineActive = false;
  currentReceiverState = {
    receiver_alias: currentConfig ? currentConfig.alias : "",
    source_name: `${String(base.sourceName || "Base source")} + ${String(overlay.sourceName || "Picture in picture")}`,
    mime_type: String(base.mimeType || mimeTypeForName(base.sourcePath)),
    media_url: String(base.mediaUrl || ""),
    playback_state: "playing",
    note: "Picture in picture staged from the source dashboard.",
  };
  headline.textContent = currentReceiverState.source_name;
  note.textContent = currentReceiverState.note;
  setImmersivePlayback(true);
  viewport.innerHTML = "";
  const stage = document.createElement("div");
  stage.className = "picture-in-picture-stage";
  const overlayFrame = document.createElement("div");
  overlayFrame.className = "picture-in-picture-overlay-frame";
  if (overlayElement.classList.contains("picture-in-picture-background-removed")) {
    overlayFrame.classList.add("picture-in-picture-background-removed");
  }
  overlayFrame.style.left = `${layout.x * 100}%`;
  overlayFrame.style.top = `${layout.y * 100}%`;
  overlayFrame.style.width = `${layout.width * 100}%`;
  overlayFrame.style.height = `${layout.height * 100}%`;
  stage.append(baseElement, overlayFrame);
  overlayFrame.appendChild(overlayElement);
  viewport.appendChild(stage);
  pictureInPictureElements = [baseElement, overlayElement];
  activePlayback = {
    mode: "picture-in-picture",
    mediaUrl: String(base.mediaUrl || ""),
    element: baseElement,
  };
  applyPlaybackState(currentReceiverState);
  setStatus("Picture in Picture");
  showRemoteFeedback(`${String(overlay.sourceName || "Overlay")} over ${String(base.sourceName || "base source")}.`);
  claimRemoteFocus();
}

async function applyFrontendStage(command) {
  const revision = String(command && command.revision || "");
  const isPictureInPicture = command && command.kind === "picture-in-picture";
  const sourcePath = isPictureInPicture
    ? String(command && command.base && command.base.sourcePath || "")
    : String(command && command.sourcePath || "");
  if (!revision || !sourcePath || revision === frontendStageRevision) {
    return;
  }
  frontendStageRevision = revision;
  frontendStageActive = true;
  if (loadLocalSourceOverrideRevision() === revision) {
    recordGitHubRefreshLog(`TV source selection retained over dashboard stage: ${sourcePath}.`);
    return;
  }
  saveLocalSourceOverrideRevision("");
  if (isPictureInPicture) {
    const overlayPath = String(command && command.overlay && command.overlay.sourcePath || "");
    recordGitHubRefreshLog(`Dashboard staged picture in picture: ${sourcePath} + ${overlayPath}.`);
    renderPictureInPictureStage(command);
    return;
  }
  recordGitHubRefreshLog(`Dashboard staged ${sourcePath}.`);
  if (isPictureInPictureRecipePath(sourcePath)) {
    try {
      await renderPictureInPictureRecipe(command);
      setStatus("Dashboard Stage");
      showRemoteFeedback(`${sourcePath} staged from the dashboard.`);
    } catch (error) {
      frontendStageActive = false;
      frontendStageRevision = "";
      renderCard("Picture in Picture Error", String(error && error.message || error || "The saved recipe could not be loaded."));
      setStatus("Picture in Picture Error", "error");
      throw error;
    }
    return;
  }
  let entry = offlineLibrary.entries.find((candidate) => candidate.id === `github:${sourcePath}`);
  if (!entry) {
    await syncGitHubOfflineLibrary({ retryFailedRevision: true });
    entry = offlineLibrary.entries.find((candidate) => candidate.id === `github:${sourcePath}`);
  }
  if (entry) {
    await renderOfflineLibraryEntry(entry, { persistSelection: true, dashboardStage: true });
    setStatus("Dashboard Stage");
    showRemoteFeedback(`${entry.name} staged from the dashboard.`);
    return;
  }
  const mimeType = mimeTypeForName(sourcePath);
  renderState({
    receiver_alias: currentConfig ? currentConfig.alias : "",
    source_name: sourcePath,
    mime_type: mimeType,
    media_url: String(command.mediaUrl || ""),
    playback_state: "playing",
    note: "Staged from the source dashboard.",
  }, { dashboardStage: true });
  setStatus("Dashboard Stage");
}

async function pollReceiverStage() {
  const target = receiverTargetId();
  const url = receiverStageUrl(target);
  if (!target || !url) {
    return;
  }
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    if (payload && payload.command) {
      await applyFrontendStage(payload.command);
    }
  } catch (error) {
    console.warn("Dashboard stage listener failed", error);
  }
}

function startReceiverStageListener() {
  if (receiverStagePollTimer) {
    clearInterval(receiverStagePollTimer);
    receiverStagePollTimer = null;
  }
  const target = receiverTargetId();
  const control = globalThis.MultiHubReceiverControl || {};
  const interval = Math.max(10000, Number(control.pollIntervalMs) || 30000);
  if (!target || !receiverStageUrl(target)) {
    if (!currentReceiverState) {
      renderCard("Receiver Setup Required", "This TV has not received its dashboard identity. Reinstall it from the receiver deployment command.");
    }
    return;
  }
  setAlias(target.replace(/^tv-/, ""));
  void pollReceiverStage();
  receiverStagePollTimer = setInterval(() => { void pollReceiverStage(); }, interval);
}

function renderCard(title, body) {
  setImmersivePlayback(false);
  viewport.innerHTML = "";
  const card = document.createElement("div");
  card.className = "card";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const bodyText = document.createElement("p");
  bodyText.textContent = body;
  card.append(heading, bodyText);
  viewport.appendChild(card);
  claimRemoteFocus();
}

function renderPlaybackError(state) {
  stopActivePlayback();
  renderCard("Video Playback Error", `${state.source_name} could not start on this TV.`);
  setStatus("Playback Error", "error");
  claimRemoteFocus();
}

function activeMediaIsSeekable() {
  return activePlayback.mode === "html5-video" || activePlayback.mode === "audio" || activePlayback.mode === "avplay" || (activePlayback.mode === "picture-in-picture" && typeof activePlayback.element?.currentTime === "number");
}

function showRemoteFeedback(text) {
  note.textContent = text;
}

function claimRemoteFocus() {
  try {
    window.focus();
  } catch (error) {}
  for (const target of [playerView, viewport, document.body, document.documentElement]) {
    try {
      target?.focus?.();
    } catch (error) {}
  }
}

function pauseAvPlay() {
  if (!hasAvPlay()) {
    return;
  }
  try {
    if (webapis.avplay.getState() === "PLAYING") {
      webapis.avplay.pause();
    }
  } catch (error) {}
}

function resumeAvPlay() {
  if (!hasAvPlay()) {
    return;
  }
  try {
    if (webapis.avplay.getState() === "PAUSED") {
      webapis.avplay.play();
    }
  } catch (error) {}
}

function hasAvPlay() {
  return typeof webapis !== "undefined" && Boolean(webapis && webapis.avplay);
}

function stopAvPlay() {
  if (!hasAvPlay()) {
    return;
  }
  try {
    const state = webapis.avplay.getState();
    if (state && state !== "NONE") {
      try {
        webapis.avplay.stop();
      } catch (error) {}
      try {
        webapis.avplay.close();
      } catch (error) {}
    }
  } catch (error) {}
}

function stopActivePlayback() {
  if (slideShowTimer) {
    clearInterval(slideShowTimer);
    slideShowTimer = null;
  }
  if (playbackProbeTimer) {
    clearTimeout(playbackProbeTimer);
    playbackProbeTimer = null;
  }
  activePlaybackOffsetSeconds = 0;
  activePlaybackOffsetApplied = false;
  currentPlaybackToken = 0;
  const primaryElement = activePlayback.element;
  if (primaryElement) {
    try {
      primaryElement.pause?.();
    } catch (error) {}
    try {
      primaryElement.removeAttribute?.("src");
      primaryElement.load?.();
    } catch (error) {}
    activePlayback.element = null;
  }
  for (const element of pictureInPictureElements) {
    if (element === primaryElement) {
      continue;
    }
    try {
      element.pause?.();
    } catch (error) {}
    try {
      element.removeAttribute?.("src");
      element.load?.();
    } catch (error) {}
  }
  pictureInPictureElements = [];
  stopAvPlay();
  if (typeof activePlayback.mediaUrl === "string" && activePlayback.mediaUrl.startsWith("blob:")) {
    try {
      URL.revokeObjectURL(activePlayback.mediaUrl);
    } catch (error) {}
  }
  activePlayback = {
    mode: null,
    mediaUrl: null,
    element: null,
  };
}

function restartActivePlaybackFromState(state) {
  activePlaybackOffsetSeconds = mediaStartOffsetSeconds(state);
  activePlaybackOffsetApplied = activePlaybackOffsetSeconds <= 0;
  const desiredState = effectivePlaybackState(state);

  if (activePlayback.mode === "html5-video" || activePlayback.mode === "audio") {
    const media = activePlayback.element;
    if (!media) {
      return;
    }
    try {
      media.pause?.();
    } catch (error) {}
    try {
      if (Math.abs((media.currentTime || 0) - activePlaybackOffsetSeconds) > 0.05) {
        media.currentTime = activePlaybackOffsetSeconds;
      }
      activePlaybackOffsetApplied = true;
    } catch (error) {}
    if (desiredState === "paused") {
      setStatus(activePlaybackOffsetSeconds > 0 ? "Ready" : "Paused");
      return;
    }
    const playResult = media.play?.();
    if (playResult && typeof playResult.catch === "function") {
      playResult.catch(() => {});
    }
    setStatus("Connected");
    return;
  }

  if (activePlayback.mode === "avplay" && hasAvPlay()) {
    const finish = () => {
      activePlaybackOffsetApplied = true;
      if (desiredState === "paused") {
        pauseAvPlay();
        setStatus(activePlaybackOffsetSeconds > 0 ? "Ready" : "Paused");
      } else {
        resumeAvPlay();
        setStatus("Connected");
      }
    };
    try {
      webapis.avplay.play();
    } catch (error) {}
    try {
      webapis.avplay.seekTo(
        Math.floor(activePlaybackOffsetSeconds * 1000),
        finish,
        finish,
      );
    } catch (error) {
      finish();
    }
  }
}

function effectivePlaybackState(state) {
  if (localPlaybackOverride === "paused" || localPlaybackOverride === "playing") {
    return localPlaybackOverride;
  }
  return String(state.playback_state || "").trim().toLowerCase() || "playing";
}

function mediaStartOffsetSeconds(state) {
  const value = Number(state.start_position_seconds || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function statePlaybackToken(state) {
  const value = Number(state.playback_token || 0);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function avPlayRect() {
  return {
    left: 0,
    top: 0,
    width: Math.max(1, Math.floor(window.innerWidth)),
    height: Math.max(1, Math.floor(window.innerHeight)),
  };
}

function syncAvPlayDisplay() {
  if (activePlayback.mode !== "avplay" || !hasAvPlay()) {
    return;
  }
  try {
    const rect = avPlayRect();
    if (typeof webapis.avplay.setDisplayMethod === "function") {
      webapis.avplay.setDisplayMethod("PLAYER_DISPLAY_MODE_FULL_SCREEN");
    }
    webapis.avplay.setDisplayRect(rect.left, rect.top, rect.width, rect.height);
  } catch (error) {
    console.warn("AVPlay display sync failed", error);
  }
}

function startVideoPlayback(video, state, onFallbackRequested) {
  const startOffsetSeconds = mediaStartOffsetSeconds(state);
  let playbackConfirmed = false;
  let fallbackRequested = false;
  let playbackRequested = false;

  const confirmPlayback = () => {
    if (playbackConfirmed) {
      return;
    }
    playbackConfirmed = true;
    if (playbackProbeTimer) {
      clearTimeout(playbackProbeTimer);
      playbackProbeTimer = null;
    }
    setStatus("Connected");
  };

  const requestFallback = () => {
    if (fallbackRequested || playbackConfirmed) {
      return;
    }
    fallbackRequested = true;
    if (playbackProbeTimer) {
      clearTimeout(playbackProbeTimer);
      playbackProbeTimer = null;
    }
    onFallbackRequested();
  };

  const applyStartOffset = () => {
    if (activePlaybackOffsetApplied || startOffsetSeconds <= 0) {
      return;
    }
    try {
      if (Math.abs(video.currentTime - startOffsetSeconds) > 0.05) {
        video.currentTime = startOffsetSeconds;
      }
      activePlaybackOffsetApplied = true;
    } catch (error) {}
  };

  const playVideo = () => {
    if (playbackRequested) {
      return;
    }
    if (effectivePlaybackState(state) === "paused") {
      if (playbackProbeTimer) {
        clearTimeout(playbackProbeTimer);
        playbackProbeTimer = null;
      }
      applyStartOffset();
      try {
        video.pause();
      } catch (error) {}
      setStatus(startOffsetSeconds > 0 ? "Ready" : "Paused");
      return;
    }
    playbackRequested = true;
    applyStartOffset();
    const playResult = video.play();
    if (!playResult || typeof playResult.catch !== "function") {
      playbackProbeTimer = setTimeout(() => {
        if (video.currentTime <= 0.05) {
          requestFallback();
        }
      }, 4000);
      return;
    }
    playResult.catch(() => {
      video.muted = true;
      video.defaultMuted = true;
      video.play().catch(() => {
        requestFallback();
      });
    });
  };

  video.addEventListener("error", () => {
    console.error("HTML5 video playback failed", video.error);
    requestFallback();
  });
  video.addEventListener("playing", confirmPlayback, { once: true });
  video.addEventListener("timeupdate", confirmPlayback, { once: true });
  video.addEventListener("loadeddata", () => {
    setStatus("Loading Video");
  }, { once: true });
  video.addEventListener("loadedmetadata", applyStartOffset, { once: true });
  video.addEventListener("stalled", requestFallback, { once: true });
  video.addEventListener("canplay", playVideo, { once: true });
  // The loop attribute handles normal HTML5 playback. Some TV Web runtimes
  // still emit "ended" for local files, so explicitly restart as a fallback.
  video.addEventListener("ended", () => {
    if (effectivePlaybackState(state) === "paused") {
      setStatus("Paused");
      return;
    }
    try {
      video.currentTime = 0;
      activePlaybackOffsetSeconds = 0;
      activePlaybackOffsetApplied = true;
    } catch (error) {}
    const playResult = video.play();
    if (playResult && typeof playResult.catch === "function") {
      playResult.catch(() => requestFallback());
    }
    setStatus("Connected");
  });
  playbackProbeTimer = setTimeout(() => {
    if (video.currentTime <= 0.05) {
      requestFallback();
    }
  }, 5000);
  video.load();
}

function renderVideoWithAvPlay(state) {
  if (!hasAvPlay()) {
    return false;
  }
  if (activePlayback.mode === "avplay" && activePlayback.mediaUrl === state.media_url) {
    syncAvPlayDisplay();
    return true;
  }

  stopActivePlayback();
  setImmersivePlayback(true);
  viewport.innerHTML = "";
  const surface = document.createElement("div");
  surface.className = "avplay-surface";
  surface.tabIndex = 0;
  viewport.appendChild(surface);
  activePlayback = {
    mode: "avplay",
    mediaUrl: state.media_url,
    element: null,
  };

  try {
    webapis.avplay.open(state.media_url);
    if (typeof webapis.avplay.setStreamingProperty === "function") {
      try {
        webapis.avplay.setStreamingProperty("ADAPTIVE_INFO", "FIXED_MAX_RESOLUTION=1920X1080");
      } catch (error) {}
    }
    webapis.avplay.setListener({
      onbufferingstart() {
        setStatus("Buffering");
      },
      onbufferingcomplete() {
        setStatus("Connected");
      },
      onstreamcompleted() {
        if (activePlayback.mode !== "avplay" || activePlayback.mediaUrl !== state.media_url) {
          return;
        }
        if (effectivePlaybackState(state) === "paused") {
          setStatus("Paused");
          return;
        }
        // AVPlay remains on the final frame after completion unless it is
        // stopped, prepared, and played again. Reuse the open player so local
        // saved files loop without re-fetching or re-opening their URI.
        activePlaybackOffsetSeconds = 0;
        activePlaybackOffsetApplied = true;
        try {
          webapis.avplay.stop();
          webapis.avplay.prepareAsync(
            () => {
              try {
                webapis.avplay.play();
                setStatus("Connected");
              } catch (error) {
                console.error("AVPlay loop restart failed", error);
                renderPlaybackError(state);
              }
            },
            (error) => {
              console.error("AVPlay loop prepare failed", error);
              renderPlaybackError(state);
            },
          );
        } catch (error) {
          console.error("AVPlay loop stop failed", error);
          renderPlaybackError(state);
        }
      },
      onerror(error) {
        console.error("AVPlay error", error);
        renderPlaybackError(state);
      },
      onerrormsg(errorType, errorMessage) {
        console.error("AVPlay error message", errorType, errorMessage);
      },
    });
    syncAvPlayDisplay();
    webapis.avplay.prepareAsync(
      () => {
        claimRemoteFocus();
        syncAvPlayDisplay();
        try {
          webapis.avplay.play();
          const finishPreparedPlayback = () => {
            if (effectivePlaybackState(state) === "paused") {
              pauseAvPlay();
              setStatus(activePlaybackOffsetSeconds > 0 ? "Ready" : "Paused");
            } else {
              resumeAvPlay();
              setStatus("Connected");
            }
          };
          if (!activePlaybackOffsetApplied && activePlaybackOffsetSeconds > 0) {
            webapis.avplay.seekTo(
              Math.floor(activePlaybackOffsetSeconds * 1000),
              () => {
                activePlaybackOffsetApplied = true;
                finishPreparedPlayback();
              },
              () => {
                finishPreparedPlayback();
              },
            );
          } else {
            finishPreparedPlayback();
          }
        } catch (error) {
          console.error("AVPlay play failed", error);
          renderPlaybackError(state);
        }
      },
      (error) => {
        console.error("AVPlay prepare failed", error);
        renderPlaybackError(state);
      },
    );
    return true;
  } catch (error) {
    console.error("AVPlay init failed", error);
    stopAvPlay();
    return false;
  }
}

function renderVideoWithHtml5(state) {
  setImmersivePlayback(true);
  viewport.innerHTML = "";
  const video = document.createElement("video");
  video.tabIndex = 0;
  video.src = state.media_url;
  video.controls = false;
  video.autoplay = effectivePlaybackState(state) !== "paused";
  video.loop = true;
  video.setAttribute("loop", "");
  video.playsInline = true;
  video.preload = "auto";
  video.setAttribute("playsinline", "true");
  video.setAttribute("webkit-playsinline", "true");
  viewport.appendChild(video);
  activePlayback = {
    mode: "html5-video",
    mediaUrl: state.media_url,
    element: video,
  };
  startVideoPlayback(video, state, () => {
    if (!state.media_url || activePlayback.mediaUrl !== state.media_url) {
      return;
    }
    if (state.offline_local && !state.offline_file) {
      renderPlaybackError(state);
      return;
    }
    if (renderVideoWithAvPlay(state)) {
      return;
    }
    renderPlaybackError(state);
  });
  claimRemoteFocus();
}

function applyPlaybackState(state) {
  const desiredState = effectivePlaybackState(state);
  if (desiredState === "paused") {
    if (activePlayback.mode === "html5-video" || activePlayback.mode === "audio") {
      try {
        activePlayback.element?.pause?.();
      } catch (error) {}
    } else if (activePlayback.mode === "picture-in-picture") {
      for (const element of pictureInPictureElements) {
        try {
          element.pause?.();
        } catch (error) {}
      }
    } else if (activePlayback.mode === "avplay") {
      pauseAvPlay();
    } else if (activePlayback.mode === "slide-show") {
      setStatus("PowerPoint Slide Show");
      return;
    }
    setStatus(activePlaybackOffsetSeconds > 0 ? "Ready" : "Paused");
    return;
  }

  if (desiredState !== "playing") {
    return;
  }

  if (activePlayback.mode === "html5-video" || activePlayback.mode === "audio") {
    const element = activePlayback.element;
    if (element) {
      const playResult = element.play?.();
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(() => {});
      }
    }
  } else if (activePlayback.mode === "picture-in-picture") {
    for (const element of pictureInPictureElements) {
      const playResult = element.play?.();
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(() => {});
      }
    }
  } else if (activePlayback.mode === "avplay") {
    if (!activePlaybackOffsetApplied && activePlaybackOffsetSeconds > 0 && hasAvPlay()) {
      try {
        webapis.avplay.play();
        webapis.avplay.seekTo(
          Math.floor(activePlaybackOffsetSeconds * 1000),
          () => {
            activePlaybackOffsetApplied = true;
            resumeAvPlay();
          },
          () => {
            resumeAvPlay();
          },
        );
        setStatus("Connected");
        return;
      } catch (error) {}
    }
    resumeAvPlay();
  } else if (activePlayback.mode === "slide-show") {
    setStatus("PowerPoint Slide Show");
    return;
  }
  setStatus("Connected");
}

function registerRemoteKeys() {
  if (typeof tizen === "undefined" || typeof tizen.tvinputdevice === "undefined") {
    return;
  }
  try {
    tizen.tvinputdevice.registerKeyBatch(REMOTE_KEYS);
  } catch (error) {
    for (const keyName of REMOTE_KEYS) {
      try {
        tizen.tvinputdevice.registerKey(keyName);
      } catch (innerError) {}
    }
  }
}

function keyNameIn(group, keyName) {
  return group.has(keyName);
}

function toggleLocalPlayback(state) {
  if (activePlayback.mode !== "html5-video" && activePlayback.mode !== "audio" && activePlayback.mode !== "avplay" && activePlayback.mode !== "picture-in-picture") {
    return;
  }
  const currentState = effectivePlaybackState(state);
  localPlaybackOverride = currentState === "paused" ? "playing" : "paused";
  applyPlaybackState(state);
  showRemoteFeedback(localPlaybackOverride === "paused" ? "Paused from TV remote." : "Playing from TV remote.");
}

function seekActivePlayback(deltaSeconds) {
  if (!activeMediaIsSeekable()) {
    return;
  }

  if (activePlayback.mode === "html5-video" || activePlayback.mode === "audio" || activePlayback.mode === "picture-in-picture") {
    const media = activePlayback.element;
    if (!media) {
      return;
    }
    const duration = Number.isFinite(media.duration) ? media.duration : null;
    const nextTime = Math.max(0, media.currentTime + deltaSeconds);
    media.currentTime = duration === null ? nextTime : Math.min(duration, nextTime);
    showRemoteFeedback(`Seek ${deltaSeconds > 0 ? "forward" : "back"} ${Math.abs(deltaSeconds)}s.`);
    return;
  }

  if (activePlayback.mode === "avplay" && hasAvPlay()) {
    try {
      const currentTimeMs = Number(webapis.avplay.getCurrentTime?.() || 0);
      const targetMs = Math.max(0, currentTimeMs + deltaSeconds * 1000);
      webapis.avplay.seekTo(targetMs, () => {}, () => {});
      showRemoteFeedback(`Seek ${deltaSeconds > 0 ? "forward" : "back"} ${Math.abs(deltaSeconds)}s.`);
    } catch (error) {}
  }
}

function handleRemoteKey(state, event) {
  const keyName = String(event.key || "");
  const code = Number(event.keyCode || event.which || 0);
  const isEnter = code === 13 || code === 10252 || keyNameIn(REMOTE_KEY_NAMES.toggle, keyName);
  const isLeft = code === 37 || keyName === "ArrowLeft";
  const isRight = code === 39 || keyName === "ArrowRight";
  const isUp = code === 38 || keyName === "ArrowUp";
  const isDown = code === 40 || keyName === "ArrowDown";

  if (refreshLogMenuOpen) {
    if (isDown || isUp || isEnter) {
      event.preventDefault();
      event.stopPropagation();
      closeRefreshLogMenu();
    }
    return;
  }

  if (sourceMenuOpen) {
    if (sourceMenuFocus === "actions") {
      if (isLeft || isRight) {
        event.preventDefault();
        event.stopPropagation();
        sourceMenuActionIndex = isLeft ? 0 : 1;
        renderSourceMenu();
        return;
      }
      if (isDown) {
        event.preventDefault();
        event.stopPropagation();
        if (sourceMenuEntries().length) {
          sourceMenuFocus = "sources";
          renderSourceMenu();
        } else {
          closeSourceMenu();
        }
        return;
      }
      if (isEnter) {
        event.preventDefault();
        event.stopPropagation();
        if (sourceMenuActionIndex === 0) {
          manuallyRefreshGitHubSources().catch((error) => console.error("Could not manually refresh GitHub sources", error));
        } else {
          openRefreshLogMenu();
        }
        return;
      }
      if (isUp) {
        event.preventDefault();
        event.stopPropagation();
      }
      return;
    }

    if (isDown) {
      event.preventDefault();
      event.stopPropagation();
      if (sourceMenuFolder) {
        sourceMenuFolder = sourceMenuParent();
        sourceMenuIndex = 0;
        renderSourceMenu();
      } else {
        closeSourceMenu();
      }
      return;
    }
    if (isLeft) {
      event.preventDefault();
      event.stopPropagation();
      moveSourceMenu(-1);
      return;
    }
    if (isRight) {
      event.preventDefault();
      event.stopPropagation();
      moveSourceMenu(1);
      return;
    }
    if (isEnter) {
      event.preventDefault();
      event.stopPropagation();
      chooseSourceMenuItem().catch((error) => {
        console.error("Could not select saved source", error);
        setStatus("Offline Storage Error", "error");
      });
      return;
    }
    if (isUp) {
      event.preventDefault();
      event.stopPropagation();
      if (sourceMenuFolder) {
        sourceMenuFolder = sourceMenuParent();
        sourceMenuIndex = 0;
      } else {
        sourceMenuFocus = "actions";
        sourceMenuActionIndex = 0;
      }
      renderSourceMenu();
      return;
    }
  }

  if (isUp) {
    event.preventDefault();
    event.stopPropagation();
    openSourceMenu();
    return;
  }
  if (isEnter) {
    event.preventDefault();
    event.stopPropagation();
    toggleLocalPlayback(state);
    return;
  }
  if (code === 415 || keyNameIn(REMOTE_KEY_NAMES.play, keyName)) {
    event.preventDefault();
    event.stopPropagation();
    localPlaybackOverride = "playing";
    applyPlaybackState(state);
    showRemoteFeedback("Playing from TV remote.");
    return;
  }
  if (code === 19 || keyNameIn(REMOTE_KEY_NAMES.pause, keyName)) {
    event.preventDefault();
    event.stopPropagation();
    localPlaybackOverride = "paused";
    applyPlaybackState(state);
    showRemoteFeedback("Paused from TV remote.");
    return;
  }
  if (code === 417 || keyNameIn(REMOTE_KEY_NAMES.fastForward, keyName)) {
    event.preventDefault();
    event.stopPropagation();
    seekActivePlayback(10);
    return;
  }
  if (code === 412 || keyNameIn(REMOTE_KEY_NAMES.rewind, keyName)) {
    event.preventDefault();
    event.stopPropagation();
    seekActivePlayback(-10);
    return;
  }
  if (code === 413 || keyNameIn(REMOTE_KEY_NAMES.stop, keyName)) {
    event.preventDefault();
    event.stopPropagation();
    localPlaybackOverride = "paused";
    applyPlaybackState(state);
    seekActivePlayback(-86400);
  }
}

function renderState(state, { offline = false, dashboardStage = false } = {}) {
  if (frontendStageActive && !dashboardStage) {
    return;
  }
  currentReceiverState = state;
  offlineActive = offline;
  const playbackToken = statePlaybackToken(state);
  const renderKey = JSON.stringify({
    source_name: state.source_name,
    mime_type: state.mime_type,
    media_url: state.media_url,
    start_position_seconds: mediaStartOffsetSeconds(state),
  });

  headline.textContent = state.source_name;
  note.textContent = state.note;
  setAlias(normalizeAlias(state.receiver_alias));
  if (state.receiver_alias) {
    saveAlias(state.receiver_alias);
  }
  if (currentRenderKey === renderKey) {
    const playbackTokenChanged = playbackToken !== currentPlaybackToken;
    currentPlaybackToken = playbackToken;
    if (playbackTokenChanged) {
      restartActivePlaybackFromState(state);
    } else {
      applyPlaybackState(state);
    }
    claimRemoteFocus();
    return;
  }
  localPlaybackOverride = null;
  currentRenderKey = renderKey;
  stopActivePlayback();
  currentPlaybackToken = playbackToken;

  if (!state.media_url) {
    renderCard(state.source_name, state.note);
    return;
  }

  viewport.innerHTML = "";
  if (state.mime_type.startsWith("image/")) {
    setImmersivePlayback(true);
    const image = document.createElement("img");
    image.src = state.media_url;
    image.alt = state.source_name;
    viewport.appendChild(image);
    activePlayback = {
      mode: "image",
      mediaUrl: state.media_url,
      element: image,
    };
    applyPlaybackState(state);
    return;
  }

  if (state.mime_type.startsWith("video/")) {
    setImmersivePlayback(true);
    activePlaybackOffsetSeconds = mediaStartOffsetSeconds(state);
    activePlaybackOffsetApplied = activePlaybackOffsetSeconds <= 0;
    renderVideoWithHtml5(state);
    applyPlaybackState(state);
    return;
  }

  if (state.mime_type.startsWith("audio/")) {
    setImmersivePlayback(false);
    const startOffsetSeconds = mediaStartOffsetSeconds(state);
    activePlaybackOffsetSeconds = startOffsetSeconds;
    activePlaybackOffsetApplied = startOffsetSeconds <= 0;
    const audio = document.createElement("audio");
    audio.src = state.media_url;
    audio.controls = true;
    audio.autoplay = effectivePlaybackState(state) !== "paused";
    if (startOffsetSeconds > 0) {
      audio.addEventListener("loadedmetadata", () => {
        try {
          audio.currentTime = startOffsetSeconds;
          activePlaybackOffsetApplied = true;
        } catch (error) {}
      }, { once: true });
    }
    viewport.appendChild(audio);
    activePlayback = {
      mode: "audio",
      mediaUrl: state.media_url,
      element: audio,
    };
    applyPlaybackState(state);
    return;
  }

  renderCard(state.source_name, state.note);
}

async function refreshGitHubSourcesOnLoad() {
  setStatus("Loading GitHub Sources");
  const { changed } = await requestGitHubSourceRefresh("startup");
  if (!changed) {
    if (!currentReceiverState) {
      setStatus("GitHub Sources Ready");
    }
    return;
  }
  if (frontendStageActive || (currentReceiverState && !offlineActive)) {
    return;
  }
  if (await renderStoredOfflineSelection()) {
    setStatus("GitHub Sources Loaded");
    return;
  }
  offlineActive = false;
  stopActivePlayback();
  if (offlineLibrary.entries.length) {
    renderCard("GitHub Sources Ready", `${offlineLibrary.entries.length} source(s) are saved on this TV. Press Up to choose one.`);
    setStatus("GitHub Sources Loaded");
  } else {
    renderCard("GitHub Source Library Empty", "No source files are currently published in the GitHub repository.");
    setStatus("GitHub Sources Empty");
  }
}

const initialConfig = loadConfig();
(async () => {
  await loadOfflineLibrary();
  await renderStoredOfflineSelection();
  currentConfig = initialConfig;
  setAlias(initialConfig.alias);
  showPlayer();
  if (!offlineActive && !currentReceiverState) {
    renderCard("Receiver Ready", "Loading GitHub sources and dashboard commands.");
  }
  startReceiverStageListener();
  void refreshGitHubSourcesOnLoad().catch((error) => {
    console.warn("GitHub source refresh was not completed", error);
    if (!currentReceiverState) {
      setStatus("GitHub Refresh Failed", "error");
    }
  });
})();
registerRemoteKeys();
claimRemoteFocus();

const handleRemoteEvent = (event) => {
  if (!currentConfig) {
    return;
  }
  handleRemoteKey(currentReceiverState || {
    source_name: "Saved Sources",
    mime_type: "text/plain",
    playback_state: "idle",
  }, event);
};

window.addEventListener("keydown", handleRemoteEvent, true);
document.addEventListener("keydown", handleRemoteEvent, true);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    claimRemoteFocus();
  }
});
document.addEventListener("pointerdown", () => {
  claimRemoteFocus();
});

window.addEventListener("resize", () => {
  syncAvPlayDisplay();
  claimRemoteFocus();
});
