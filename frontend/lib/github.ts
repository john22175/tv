import "server-only";

import { assertSourceFilename, sourcePath } from "@/lib/sources";

export type SourceRecord = {
  name: string;
  path: string;
  sha: string;
  size: number;
  downloadUrl: string;
  htmlUrl: string;
};

type GitHubContent = {
  type: string;
  name: string;
  path: string;
  sha: string;
  size: number;
  download_url: string | null;
  html_url: string | null;
};

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
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function listSources(): Promise<SourceRecord[]> {
  const { owner, repository, branch } = config();
  const content = await request<GitHubContent[]>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/sources?ref=${encodeURIComponent(branch)}`,
  );
  return content
    .filter((item) => item.type === "file")
    .map((item) => ({
      name: item.name,
      path: item.path,
      sha: item.sha,
      size: item.size,
      downloadUrl: item.download_url || "",
      htmlUrl: item.html_url || "",
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

export async function sourceExists(filename: string): Promise<boolean> {
  const { owner, repository, branch } = config();
  const response = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourcePath(filename))}?ref=${encodeURIComponent(branch)}`,
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
  if (response.status === 404) {
    return false;
  }
  if (!response.ok) {
    throw new Error(`GitHub lookup failed (${response.status}).`);
  }
  return true;
}

export async function dispatchSourcePublish(input: { filename: string; uploadUrl: string; requestId: string }): Promise<void> {
  const filename = assertSourceFilename(input.filename);
  const { owner, repository, branch } = config();
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/actions/workflows/publish-source.yml/dispatches`,
    {
      method: "POST",
      body: JSON.stringify({
        ref: branch,
        inputs: { filename, upload_url: input.uploadUrl, request_id: input.requestId },
      }),
    },
  );
}

export async function deleteSource(input: { filename: string; sha: string }): Promise<void> {
  const filename = assertSourceFilename(input.filename);
  if (!/^[0-9a-f]{40,64}$/i.test(input.sha)) {
    throw new Error("The source revision is invalid. Refresh the source list and try again.");
  }
  const { owner, repository, branch } = config();
  await request<void>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourcePath(filename))}`,
    {
      method: "DELETE",
      body: JSON.stringify({
        message: `source-manager: delete ${filename}`,
        sha: input.sha,
        branch,
      }),
    },
  );
}
