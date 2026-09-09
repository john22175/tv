import "server-only";

import { revalidateTag, unstable_cache } from "next/cache";

import { createGitHubInstallationToken } from "@/lib/github-app";
import { RECEIVERS, type ReceiverId, type ReceiverStageCommand } from "@/lib/receiver-types";

const MANIFEST_PATH = "receiver-manifest.json";
const MANIFEST_CACHE_TAG = "tv-control-manifest";

type ControlManifest = {
  version: 1;
  updatedAt: string;
  receivers: Partial<Record<ReceiverId, ReceiverStageCommand | null>>;
};

type GitHubContent = { content?: string; encoding?: string; sha?: string };

class GitHubControlError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function controlConfig() {
  const owner = process.env.CONTROL_GITHUB_OWNER?.trim();
  const repository = process.env.CONTROL_GITHUB_REPOSITORY?.trim();
  const branch = process.env.CONTROL_GITHUB_BRANCH?.trim() || "main";
  if (!owner || !repository) {
    throw new Error("CONTROL_GITHUB_OWNER and CONTROL_GITHUB_REPOSITORY must be configured.");
  }
  return { owner, repository, branch };
}

function endpoint(path: string) {
  const { owner, repository } = controlConfig();
  return `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { token } = await createGitHubInstallationToken("GITHUB_CONTROL");
  const response = await fetch(endpoint(path), {
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
    throw new GitHubControlError(response.status, `GitHub control request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

function emptyManifest(): ControlManifest {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    receivers: Object.fromEntries(RECEIVERS.map((receiver) => [receiver.id, null])) as ControlManifest["receivers"],
  };
}

function isReceiverId(value: string): value is ReceiverId {
  return RECEIVERS.some((receiver) => receiver.id === value);
}

function normalizeManifest(value: unknown): ControlManifest {
  const candidate = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const candidateReceivers = candidate.receivers && typeof candidate.receivers === "object"
    ? candidate.receivers as Record<string, ReceiverStageCommand | null>
    : {};
  const receivers = Object.fromEntries(RECEIVERS.map((receiver) => [
    receiver.id,
    candidateReceivers[receiver.id] || null,
  ])) as ControlManifest["receivers"];
  return {
    version: 1,
    updatedAt: typeof candidate.updatedAt === "string" ? candidate.updatedAt : new Date().toISOString(),
    receivers,
  };
}

async function readManifestUncached(): Promise<{ manifest: ControlManifest; sha: string | null }> {
  const { branch } = controlConfig();
  try {
    const response = await request<GitHubContent>(`/contents/${MANIFEST_PATH}?ref=${encodeURIComponent(branch)}`);
    if (!response.content || response.encoding !== "base64") {
      throw new Error("GitHub control manifest did not contain Base64 JSON.");
    }
    const body = Buffer.from(response.content.replace(/\s/g, ""), "base64").toString("utf8");
    return { manifest: normalizeManifest(JSON.parse(body)), sha: response.sha || null };
  } catch (error) {
    if (error instanceof GitHubControlError && error.status === 404) {
      return { manifest: emptyManifest(), sha: null };
    }
    throw error;
  }
}

const readManifestCached = unstable_cache(
  async () => (await readManifestUncached()).manifest,
  ["tv-control-manifest"],
  { revalidate: 60, tags: [MANIFEST_CACHE_TAG] },
);

export async function readReceiverCommand(receiverId: ReceiverId): Promise<ReceiverStageCommand | null> {
  const manifest = await readManifestCached();
  return manifest.receivers[receiverId] || null;
}

export async function readAllReceiverCommands(): Promise<Partial<Record<ReceiverId, ReceiverStageCommand | null>>> {
  return (await readManifestCached()).receivers;
}

export async function updateReceiverCommands(updates: Record<ReceiverId, ReceiverStageCommand>): Promise<void> {
  for (const receiverId of Object.keys(updates)) {
    if (!isReceiverId(receiverId)) throw new Error("Control manifest includes an unknown receiver.");
  }
  const { branch } = controlConfig();
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const { manifest, sha } = await readManifestUncached();
    for (const [receiverId, command] of Object.entries(updates) as Array<[ReceiverId, ReceiverStageCommand]>) {
      manifest.receivers[receiverId] = command;
    }
    manifest.updatedAt = new Date().toISOString();
    try {
      await request(`/contents/${MANIFEST_PATH}`, {
        method: "PUT",
        body: JSON.stringify({
          message: `tv-control: stage ${Object.keys(updates).join(", ")}`,
          content: Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`).toString("base64"),
          branch,
          ...(sha ? { sha } : {}),
        }),
      });
      revalidateTag(MANIFEST_CACHE_TAG, "max");
      return;
    } catch (error) {
      lastError = error;
      if (!(error instanceof GitHubControlError) || error.status !== 409) throw error;
    }
  }
  throw lastError instanceof Error ? lastError : new Error("Could not update the receiver manifest.");
}
