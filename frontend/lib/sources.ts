export const SOURCE_DIRECTORY = "sources";
export const SOURCE_MAX_BYTES = 95 * 1024 * 1024;

const SUPPORTED_EXTENSIONS = new Set([
  "mp4", "mov", "m4v", "webm", "mp3", "wav", "ogg",
  "jpg", "jpeg", "png", "gif", "bmp", "webp", "pdf", "ppt", "pptx",
]);

export class SourceValidationError extends Error {}

export function assertSourceFilename(value: unknown): string {
  if (typeof value !== "string") {
    throw new SourceValidationError("A source filename is required.");
  }

  const filename = value.normalize("NFC").trim();
  if (!filename || filename.length > 120 || filename === "." || filename === "..") {
    throw new SourceValidationError("Use a filename between 1 and 120 characters.");
  }
  if (filename.startsWith(".") || filename.includes("/") || filename.includes("\\") || filename.includes("\0")) {
    throw new SourceValidationError("Source files must use a visible flat filename.");
  }
  if (!/^[\p{L}\p{N}][\p{L}\p{N} ._()'&-]*$/u.test(filename)) {
    throw new SourceValidationError("The filename contains unsupported characters.");
  }

  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    throw new SourceValidationError(`Unsupported source type: .${extension || "(none)"}.`);
  }
  return filename;
}

export function sourcePath(filename: string): string {
  return `${SOURCE_DIRECTORY}/${assertSourceFilename(filename)}`;
}

export function assertSourceSize(size: unknown): number {
  const parsed = Number(size);
  if (!Number.isSafeInteger(parsed) || parsed <= 0 || parsed > SOURCE_MAX_BYTES) {
    throw new SourceValidationError(`Source files must be between 1 byte and ${SOURCE_MAX_BYTES} bytes.`);
  }
  return parsed;
}

export function sourceMimeType(filename: string): string {
  const extension = assertSourceFilename(filename).split(".").pop()?.toLowerCase();
  const types: Record<string, string> = {
    mp4: "video/mp4", mov: "video/quicktime", m4v: "video/x-m4v", webm: "video/webm",
    mp3: "audio/mpeg", wav: "audio/wav", ogg: "audio/ogg",
    jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif",
    bmp: "image/bmp", webp: "image/webp", pdf: "application/pdf",
    ppt: "application/vnd.ms-powerpoint",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  };
  return types[extension ?? ""] ?? "application/octet-stream";
}
