import "server-only";

import { BlobNotFoundError, head, put } from "@vercel/blob";

import { listSources, type SourceRecord } from "@/lib/github";
import { assertSourcePath, SourceValidationError } from "@/lib/sources";

export const RECEIVERS = [
  { id: "tv-1", label: "TV 1", host: "10.171.64.177" },
  { id: "tv-2", label: "TV 2", host: "10.171.64.176" },
  { id: "tv-3", label: "TV 3", host: "10.171.64.175" },
  { id: "tv-4", label: "TV 4", host: "10.171.64.63" },
  { id: "tv-5", label: "TV 5", host: "10.171.64.174" },
  { id: "tv-6", label: "TV 6", host: "10.171.64.167" },
] as const;

export type ReceiverId = (typeof RECEIVERS)[number]["id"];

export type ReceiverStage = {
  revision: string;
  sourcePath: string;
  sourceName: string;
  sourceSha: string;
  mediaUrl: string;
  stagedAt: string;
};

export type ReceiverStatus = {
  receiverId: ReceiverId;
  lastSeenAt: string;
  commandRevision: string | null;
};

const HEARTBEAT_WRITE_INTERVAL_MS = 5 * 60 * 1000;
export const RECEIVER_POLL_INTERVAL_MS = 30 * 1000;

function assertReceiverId(value: string): ReceiverId {
  if (!RECEIVERS.some((receiver) => receiver.id === value)) {
    throw new SourceValidationError("Unknown TV receiver.");
  }
  return value as ReceiverId;
}

function stagePath(receiverId: ReceiverId) {
  return `receiver-stage/${receiverId}.json`;
}

function statusPath(receiverId: ReceiverId) {
  return `receiver-status/${receiverId}.json`;
}

async function readBlobJson<T>(pathname: string): Promise<T | null> {
  try {
    const metadata = await head(pathname);
    const response = await fetch(metadata.url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Blob read failed (${response.status}).`);
    return response.json() as Promise<T>;
  } catch (error) {
    if (error instanceof BlobNotFoundError || (error instanceof Error && error.name === "BlobNotFoundError")) {
      return null;
    }
    throw error;
  }
}

async function writeBlobJson(pathname: string, value: unknown) {
  await put(pathname, JSON.stringify(value), {
    access: "public",
    addRandomSuffix: false,
    allowOverwrite: true,
    contentType: "application/json; charset=utf-8",
    cacheControlMaxAge: 0,
  });
}

function statusIsFresh(status: ReceiverStatus | null): boolean {
  if (!status) return false;
  const seen = Date.parse(status.lastSeenAt);
  return Number.isFinite(seen) && Date.now() - seen < HEARTBEAT_WRITE_INTERVAL_MS;
}

async function touchReceiver(receiverId: ReceiverId, commandRevision: string | null): Promise<void> {
  const current = await readBlobJson<ReceiverStatus>(statusPath(receiverId));
  if (statusIsFresh(current) && current?.commandRevision === commandRevision) return;
  await writeBlobJson(statusPath(receiverId), {
    receiverId,
    lastSeenAt: new Date().toISOString(),
    commandRevision,
  } satisfies ReceiverStatus);
}

export async function listReceiverStatuses(): Promise<Array<(typeof RECEIVERS)[number] & { online: boolean; lastSeenAt: string | null; commandRevision: string | null; pollIntervalMs: number }>> {
  const now = Date.now();
  return Promise.all(RECEIVERS.map(async (receiver) => {
    const status = await readBlobJson<ReceiverStatus>(statusPath(receiver.id));
    const seen = status ? Date.parse(status.lastSeenAt) : Number.NaN;
    return {
      ...receiver,
      online: Number.isFinite(seen) && now - seen < HEARTBEAT_WRITE_INTERVAL_MS * 2,
      lastSeenAt: status?.lastSeenAt || null,
      commandRevision: status?.commandRevision || null,
      pollIntervalMs: RECEIVER_POLL_INTERVAL_MS,
    };
  }));
}

export async function stageSourceForReceiver(input: { receiverId: string; sourcePath: string }): Promise<ReceiverStage> {
  const receiverId = assertReceiverId(input.receiverId);
  const sourcePath = assertSourcePath(input.sourcePath);
  const source = (await listSources()).find((entry): entry is SourceRecord => entry.kind === "file" && entry.path === sourcePath);
  if (!source) throw new SourceValidationError("That source no longer exists. Refresh the library and try again.");
  const command: ReceiverStage = {
    revision: crypto.randomUUID(),
    sourcePath,
    sourceName: source.name,
    sourceSha: source.sha,
    mediaUrl: source.downloadUrl,
    stagedAt: new Date().toISOString(),
  };
  await writeBlobJson(stagePath(receiverId), command);
  return command;
}

export async function getReceiverStage(receiverIdInput: string): Promise<ReceiverStage | null> {
  const receiverId = assertReceiverId(receiverIdInput);
  const stage = await readBlobJson<ReceiverStage>(stagePath(receiverId));
  await touchReceiver(receiverId, stage?.revision || null);
  return stage;
}
