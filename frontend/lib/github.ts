import "server-only";

import {
  assertSourceDirectory,
  assertSourcePath,
  isInternalSourcePath,
  sourceMarkerPath,
  sourcePath,
  SourceValidationError,
} from "@/lib/sources";

export type SourceRecord = {
  kind: "file" | "folder";
  name: string;
  /** Path relative to sources/. */
  path: string;
  sha: string;
  size: number;
  downloadUrl: string;
  htmlUrl: string;
};

type GitHubTreeNode = {
  path: string;
  mode: string;
  type: "blob" | "tree" | "commit";
  sha: string;
  size?: number;
};

type GitHubTree = { tree: GitHubTreeNode[]; truncated?: boolean; sha?: string };
type GitHubCommit = { commit: { tree: { sha: string } } };
type GitHubRef = { object: { sha: string } };
type GitHubContent = { sha: string };

function config() {
  const token = process.env.GITHUB_SOURCE_MANAGER_TOKEN?.trim();
  if (!token) {
    throw new Error("GITHUB_SOURCE_MANAGER_TOKEN is not configured.");
  }
  return {
    token,
    owner: process.env.GITHUB_OWNER?.trim() || "john22175",
    repository: process.env.GITHUB_REPOSITORY?.trim() || "tv",
    branch: process.env.GITHUB_BRANCH?.trim() || "main",
  };
}

function encodedPath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function sourceWebUrl(path: string): string {
  const { owner, repository, branch } = config();
  return `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/blob/${encodeURIComponent(branch)}/${encodedPath(sourcePath(path))}`;
}

function sourceRawUrl(path: string): string {
  const { owner, repository, branch } = config();
  return `https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/${encodeURIComponent(branch)}/${encodedPath(sourcePath(path))}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { token } = config();
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "tv-source-dashboard",
      ...init.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`GitHub request failed (${response.status}): ${detail || response.statusText}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function sourcesTree(): Promise<GitHubTree> {
  const { owner, repository, branch } = config();
  const commit = await request<GitHubCommit>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/commits/${encodeURIComponent(branch)}`,
  );
  const root = await request<GitHubTree>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/trees/${encodeURIComponent(commit.commit.tree.sha)}`,
  );
  const sources = root.tree.find((node) => node.path === "sources" && node.type === "tree");
  if (!sources) return { tree: [] };
  const tree = await request<GitHubTree>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/trees/${encodeURIComponent(sources.sha)}?recursive=1`,
  );
  if (tree.truncated) throw new Error("The sources directory is too large to list safely.");
  return tree;
}

export async function listSources(): Promise<SourceRecord[]> {
  const tree = await sourcesTree();
  const entries: SourceRecord[] = [];
  for (const node of tree.tree) {
    if (node.type !== "blob" || !node.path) continue;
    const relative = node.path;
    if (relative.endsWith("/.keep")) {
      const folder = relative.slice(0, -"/.keep".length);
      try {
        const safeFolder = assertSourceDirectory(folder);
        entries.push({
          kind: "folder",
          name: safeFolder.split("/").at(-1) || safeFolder,
          path: safeFolder,
          sha: node.sha,
          size: 0,
          downloadUrl: "",
          htmlUrl: "",
        });
      } catch { /* Invalid internal markers are not published. */ }
      continue;
    }
    if (isInternalSourcePath(relative)) continue;
    try {
      const path = assertSourcePath(relative);
      entries.push({
        kind: "file",
        name: path.split("/").at(-1) || path,
        path,
        sha: node.sha,
        size: Number(node.size || 0),
        downloadUrl: sourceRawUrl(path),
        htmlUrl: sourceWebUrl(path),
      });
    } catch { /* Only supported public source files appear in the dashboard. */ }
  }
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}

export async function sourceExists(relativePath: string): Promise<boolean> {
  const { owner, repository, branch } = config();
  const safePath = assertSourcePath(relativePath);
  const response = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourcePath(safePath))}?ref=${encodeURIComponent(branch)}`,
    {
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${config().token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tv-source-dashboard",
      },
      cache: "no-store",
    },
  );
  if (response.status === 404) return false;
  if (!response.ok) throw new Error(`GitHub lookup failed (${response.status}).`);
  return true;
}

export async function dispatchSourcePublish(input: { path: string; uploadUrl: string; requestId: string }): Promise<void> {
  const path = assertSourcePath(input.path);
  const { owner, repository, branch } = config();
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/actions/workflows/publish-source.yml/dispatches`,
    {
      method: "POST",
      body: JSON.stringify({ ref: branch, inputs: { source_path: path, upload_url: input.uploadUrl, request_id: input.requestId } }),
    },
  );
}

export async function deleteSource(input: { path: string; sha: string }): Promise<void> {
  const path = assertSourcePath(input.path);
  if (!/^[0-9a-f]{40,64}$/i.test(input.sha)) {
    throw new Error("The source revision is invalid. Refresh the source list and try again.");
  }
  const { owner, repository, branch } = config();
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourcePath(path))}`,
    {
      method: "DELETE",
      body: JSON.stringify({ message: `source-manager: delete ${path}`, sha: input.sha, branch }),
    },
  );
}

export async function createSourceFolder(input: { path: string }): Promise<void> {
  const folder = assertSourceDirectory(input.path);
  const { owner, repository, branch } = config();
  const marker = sourceMarkerPath(folder);
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(marker)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        message: `source-manager: create folder ${folder}`,
        content: Buffer.from("MultiHub source folder\n").toString("base64"),
        branch,
      }),
    },
  );
}

export async function deleteSourceFolder(input: { path: string; markerSha: string }): Promise<void> {
  const folder = assertSourceDirectory(input.path);
  if (!/^[0-9a-f]{40,64}$/i.test(input.markerSha)) {
    throw new Error("The folder revision is invalid. Refresh the source list and try again.");
  }
  const all = await listSources();
  if (all.some((entry) => entry.kind === "file" && entry.path.startsWith(`${folder}/`))) {
    throw new SourceValidationError("Move or delete the files in this folder before deleting it.");
  }
  const { owner, repository, branch } = config();
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourceMarkerPath(folder))}`,
    {
      method: "DELETE",
      body: JSON.stringify({ message: `source-manager: delete folder ${folder}`, sha: input.markerSha, branch }),
    },
  );
}

export async function moveSource(input: { fromPath: string; toPath: string; sha: string }): Promise<void> {
  const fromPath = assertSourcePath(input.fromPath);
  const toPath = assertSourcePath(input.toPath);
  if (fromPath === toPath) return;
  if (!/^[0-9a-f]{40,64}$/i.test(input.sha)) {
    throw new SourceValidationError("The source revision is invalid. Refresh the source list and try again.");
  }
  if (await sourceExists(toPath)) {
    throw new SourceValidationError("A source with that name already exists in the destination folder.");
  }
  const { owner, repository, branch } = config();
  const ref = await request<GitHubRef>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/ref/heads/${encodedPath(branch)}`,
  );
  const commit = await request<{ tree: { sha: string } }>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/commits/${encodeURIComponent(ref.object.sha)}`,
  );
  const tree = await request<{ sha: string }>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/trees`,
    {
      method: "POST",
      body: JSON.stringify({
        base_tree: commit.tree.sha,
        tree: [
          { path: sourcePath(toPath), mode: "100644", type: "blob", sha: input.sha },
          { path: sourcePath(fromPath), mode: "100644", type: "blob", sha: null },
        ],
      }),
    },
  );
  const newCommit = await request<{ sha: string }>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/commits`,
    {
      method: "POST",
      body: JSON.stringify({ message: `source-manager: move ${fromPath} to ${toPath}`, tree: tree.sha, parents: [ref.object.sha] }),
    },
  );
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/git/refs/heads/${encodedPath(branch)}`,
    { method: "PATCH", body: JSON.stringify({ sha: newCommit.sha, force: false }) },
  );
}
