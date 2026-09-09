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
  kind?: "source";
  revision: string;
  sourcePath: string;
  sourceName: string;
  sourceSha: string;
  mediaUrl: string;
  stagedAt: string;
};

export type ReceiverPictureInPictureStage = {
  kind: "picture-in-picture";
  revision: string;
  base: ReceiverStageSource;
  overlay: ReceiverStageSource;
  layout: { x: number; y: number; width: number; height: number };
  stagedAt: string;
};

export type ReceiverStageSource = {
  sourcePath: string;
  sourceName: string;
  sourceSha: string;
  mediaUrl: string;
  mimeType: string;
};

export type ReceiverStageCommand = ReceiverStage | ReceiverPictureInPictureStage;
