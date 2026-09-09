import "server-only";

import { readAllReceiverCommands, readReceiverCommand, updateReceiverCommands } from "@/lib/control";
import { listSources, type SourceRecord } from "@/lib/github";
import {
  RECEIVERS,
  type ReceiverId,
  type ReceiverPictureInPictureStage,
  type ReceiverStage,
  type ReceiverStageCommand,
  type ReceiverStageSource,
} from "@/lib/receiver-types";
import { assertSourcePath, sourceMimeType, SourceValidationError } from "@/lib/sources";

export { RECEIVERS };
export type {
  ReceiverId,
  ReceiverPictureInPictureStage,
  ReceiverStage,
  ReceiverStageCommand,
  ReceiverStageSource,
};

/** The receiver checks the command endpoint once per minute. */
export const RECEIVER_POLL_INTERVAL_MS = 60 * 1000;

export type ReceiverStatus = {
  receiverId: ReceiverId;
  commandRevision: string | null;
  stagedAt: string | null;
};

function assertReceiverId(value: string): ReceiverId {
  if (!RECEIVERS.some((receiver) => receiver.id === value)) {
    throw new SourceValidationError("Unknown TV receiver.");
  }
  return value as ReceiverId;
}

function commandStagedAt(command: ReceiverStageCommand | null): string | null {
  return command?.stagedAt || null;
}

/**
 * This intentionally does not claim to know whether a TV is online. The old
 * Blob heartbeat was removed to eliminate background write operations.
 */
export async function listReceiverStatuses(): Promise<Array<(typeof RECEIVERS)[number] & ReceiverStatus & { pollIntervalMs: number }>> {
  const commands = await readAllReceiverCommands();
  return RECEIVERS.map((receiver) => ({
    ...receiver,
    receiverId: receiver.id,
    commandRevision: commands[receiver.id]?.revision || null,
    stagedAt: commandStagedAt(commands[receiver.id] || null),
    pollIntervalMs: RECEIVER_POLL_INTERVAL_MS,
  }));
}

async function sourceForStage(pathInput: string): Promise<SourceRecord> {
  const sourcePath = assertSourcePath(pathInput);
  const source = (await listSources()).find((entry): entry is SourceRecord => entry.kind === "file" && entry.path === sourcePath);
  if (!source) throw new SourceValidationError("That source no longer exists. Refresh the library and try again.");
  return source;
}

function sourceCommand(source: SourceRecord): ReceiverStage {
  return {
    revision: crypto.randomUUID(),
    sourcePath: source.path,
    sourceName: source.name,
    sourceSha: source.sha,
    mediaUrl: source.downloadUrl,
    stagedAt: new Date().toISOString(),
  };
}

function clampLayoutNumber(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(maximum, Math.max(minimum, number));
}

function pictureInPictureLayout(input: unknown) {
  const candidate = input && typeof input === "object" ? input as Record<string, unknown> : {};
  const width = clampLayoutNumber(candidate.width, 0.3, 0.12, 0.88);
  const height = clampLayoutNumber(candidate.height, 0.3, 0.12, 0.88);
  return {
    x: clampLayoutNumber(candidate.x, 0.64, 0, 1 - width),
    y: clampLayoutNumber(candidate.y, 0.06, 0, 1 - height),
    width,
    height,
  };
}

async function pictureInPictureSource(pathInput: string): Promise<ReceiverStageSource> {
  const source = await sourceForStage(pathInput);
  const mimeType = sourceMimeType(source.path);
  if (!mimeType.startsWith("video/") && !mimeType.startsWith("image/")) {
    throw new SourceValidationError("Picture-in-picture supports image and video sources only.");
  }
  return {
    sourcePath: source.path,
    sourceName: source.name,
    sourceSha: source.sha,
    mediaUrl: source.downloadUrl,
    mimeType,
  };
}

function uniqueReceiverIds(receiverIds: string[]): ReceiverId[] {
  return [...new Set(receiverIds.map(assertReceiverId))];
}

export async function stageSourceForReceivers(input: { receiverIds: string[]; sourcePath: string }): Promise<ReceiverStage[]> {
  const receiverIds = uniqueReceiverIds(input.receiverIds);
  if (!receiverIds.length) throw new SourceValidationError("Choose at least one TV receiver.");
  const source = await sourceForStage(input.sourcePath);
  const commands = Object.fromEntries(receiverIds.map((receiverId) => [receiverId, sourceCommand(source)])) as Record<ReceiverId, ReceiverStage>;
  await updateReceiverCommands(commands);
  return receiverIds.map((receiverId) => commands[receiverId]);
}

export async function stageSourceForReceiver(input: { receiverId: string; sourcePath: string }): Promise<ReceiverStage> {
  return (await stageSourceForReceivers({ receiverIds: [input.receiverId], sourcePath: input.sourcePath }))[0];
}

export async function stagePictureInPictureForReceivers(input: {
  receiverIds: string[];
  baseSourcePath: string;
  overlaySourcePath: string;
  layout: unknown;
}): Promise<ReceiverPictureInPictureStage[]> {
  const receiverIds = uniqueReceiverIds(input.receiverIds);
  if (!receiverIds.length) throw new SourceValidationError("Choose at least one TV receiver.");
  const [base, overlay] = await Promise.all([
    pictureInPictureSource(input.baseSourcePath),
    pictureInPictureSource(input.overlaySourcePath),
  ]);
  if (base.sourcePath === overlay.sourcePath) {
    throw new SourceValidationError("Choose two different sources for picture-in-picture.");
  }
  const commands = Object.fromEntries(receiverIds.map((receiverId) => [receiverId, {
    kind: "picture-in-picture" as const,
    revision: crypto.randomUUID(),
    base,
    overlay,
    layout: pictureInPictureLayout(input.layout),
    stagedAt: new Date().toISOString(),
  }])) as Record<ReceiverId, ReceiverPictureInPictureStage>;
  await updateReceiverCommands(commands);
  return receiverIds.map((receiverId) => commands[receiverId]);
}

export async function stagePictureInPictureForReceiver(input: {
  receiverId: string;
  baseSourcePath: string;
  overlaySourcePath: string;
  layout: unknown;
}): Promise<ReceiverPictureInPictureStage> {
  return (await stagePictureInPictureForReceivers({ ...input, receiverIds: [input.receiverId] }))[0];
}

export async function getReceiverStage(receiverIdInput: string): Promise<ReceiverStageCommand | null> {
  return readReceiverCommand(assertReceiverId(receiverIdInput));
}
