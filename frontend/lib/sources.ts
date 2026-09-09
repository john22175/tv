export const SOURCE_DIRECTORY = "sources";
export const SOURCE_MAX_BYTES = 95 * 1024 * 1024;
export const SOURCE_MAX_DEPTH = 4;
export const SOURCE_FOLDER_MARKER = ".keep";
/** Durable location for the one reusable picture-in-picture composition. */
export const PICTURE_IN_PICTURE_DIRECTORY = "Welcome/Temp";
export const PICTURE_IN_PICTURE_RECIPE_FILENAME = "Welcome_Filled.pip.json";
export const PICTURE_IN_PICTURE_OVERLAY_FILENAME = "Welcome_Filled_overlay.png";
export const PICTURE_IN_PICTURE_RECIPE_MIME_TYPE = "application/vnd.multihub.picture-in-picture+json";
export const SLIDE_SHOW_RECIPE_MIME_TYPE = "application/vnd.multihub.slide-show+json";

const SUPPORTED_EXTENSIONS = new Set([
  "mp4", "mov", "m4v", "webm", "mp3", "wav", "ogg",
  "jpg", "jpeg", "png", "gif", "bmp", "webp", "pdf", "ppt", "pptx",
]);

export class SourceValidationError extends Error {}

/** A generated PiP recipe is a first-class source, not an uploaded document. */
export function isPictureInPictureRecipePath(value: string): boolean {
  return value.toLocaleLowerCase("en-US").endsWith(".pip.json");
}

/** A generated manifest that tells receivers to loop rendered PowerPoint slides. */
export function isSlideShowRecipePath(value: string): boolean {
  return value.toLocaleLowerCase("en-US").endsWith(".slides.json");
}

export function isPresentationPath(value: string): boolean {
  return /\.(ppt|pptx)$/i.test(value);
}

/** GitHub Actions writes this beside the uploaded presentation. */
export function slideShowRecipePathForPresentation(value: string): string {
  const source = assertSourcePath(value);
  if (!isPresentationPath(source)) {
    throw new SourceValidationError("A PowerPoint .ppt or .pptx file is required.");
  }
  return source.replace(/\.(ppt|pptx)$/i, ".slides.json");
}

function normalizedSegment(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new SourceValidationError(`${label} is required.`);
  }
  const segment = value.normalize("NFC").trim();
  if (!segment || segment.length > 120 || segment === "." || segment === "..") {
    throw new SourceValidationError(`${label} must be between 1 and 120 characters.`);
  }
  if (segment.startsWith(".") || segment.includes("/") || segment.includes("\\") || segment.includes("\0")) {
    throw new SourceValidationError(`${label} must be visible and cannot contain a path separator.`);
  }
  if (!/^[\p{L}\p{N}][\p{L}\p{N} ._()'&-]*$/u.test(segment)) {
    throw new SourceValidationError(`${label} contains unsupported characters.`);
  }
  return segment;
}

export function assertSourceFilename(value: unknown): string {
  const filename = normalizedSegment(value, "Source filename");
  if (isPictureInPictureRecipePath(filename) || isSlideShowRecipePath(filename)) {
    return filename;
  }
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    throw new SourceValidationError(`Unsupported source type: .${extension || "(none)"}.`);
  }
  return filename;
}

export function assertSourceDirectory(value: unknown): string {
  if (typeof value !== "string") {
    throw new SourceValidationError("Folder path is required.");
  }
  const directory = value.normalize("NFC").trim().replace(/^\/+|\/+$/g, "");
  if (!directory) {
    throw new SourceValidationError("A folder name is required.");
  }
  const parts = directory.split("/");
  if (parts.length > SOURCE_MAX_DEPTH) {
    throw new SourceValidationError(`Folders may be nested at most ${SOURCE_MAX_DEPTH} levels.`);
  }
  return parts.map((part) => normalizedSegment(part, "Folder name")).join("/");
}

/** Validate a path relative to sources/, such as "Lobby/Welcome.mp4". */
export function assertSourcePath(value: unknown): string {
  if (typeof value !== "string") {
    throw new SourceValidationError("Source path is required.");
  }
  const source = value.normalize("NFC").trim().replace(/^\/+|\/+$/g, "");
  const parts = source.split("/");
  if (parts.length < 1 || parts.length > SOURCE_MAX_DEPTH + 1) {
    throw new SourceValidationError(`Sources may be placed at most ${SOURCE_MAX_DEPTH} folders deep.`);
  }
  const filename = assertSourceFilename(parts.pop());
  const directory = parts.length ? assertSourceDirectory(parts.join("/")) : "";
  return directory ? `${directory}/${filename}` : filename;
}

export function sourcePath(relativePath: string): string {
  return `${SOURCE_DIRECTORY}/${assertSourcePath(relativePath)}`;
}

/** The generated PiP recipe always has one durable, reusable location. */
export function pictureInPictureRecipePath(): string {
  return `${PICTURE_IN_PICTURE_DIRECTORY}/${PICTURE_IN_PICTURE_RECIPE_FILENAME}`;
}

/** Clipboard overlays use one PNG path and are replaced rather than accumulated. */
export function pictureInPictureOverlayPath(): string {
  return `${PICTURE_IN_PICTURE_DIRECTORY}/${PICTURE_IN_PICTURE_OVERLAY_FILENAME}`;
}

/** These two generated files are replaced together, never moved or deleted from the library UI. */
export function isManagedPictureInPicturePath(path: string): boolean {
  return path === pictureInPictureRecipePath() || path === pictureInPictureOverlayPath();
}

export function sourceFolderPath(relativeDirectory: string): string {
  return `${SOURCE_DIRECTORY}/${assertSourceDirectory(relativeDirectory)}`;
}

export function sourceMarkerPath(relativeDirectory: string): string {
  return `${sourceFolderPath(relativeDirectory)}/${SOURCE_FOLDER_MARKER}`;
}

export function isInternalSourcePath(relativePath: string): boolean {
  return relativePath.split("/").some((part) => part.startsWith("."));
}

export function assertSourceSize(size: unknown): number {
  const parsed = Number(size);
  if (!Number.isSafeInteger(parsed) || parsed <= 0 || parsed > SOURCE_MAX_BYTES) {
    throw new SourceValidationError(`Source files must be between 1 byte and ${SOURCE_MAX_BYTES} bytes.`);
  }
  return parsed;
}

export function sourceMimeType(relativePath: string): string {
  const path = assertSourcePath(relativePath);
  if (isPictureInPictureRecipePath(path)) return PICTURE_IN_PICTURE_RECIPE_MIME_TYPE;
  if (isSlideShowRecipePath(path)) return SLIDE_SHOW_RECIPE_MIME_TYPE;
  const extension = path.split(".").pop()?.toLowerCase();
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
