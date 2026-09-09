import { createHash } from "node:crypto";

import { NextResponse } from "next/server";

import { listSources } from "@/lib/github";
import { isSlideShowRecipePath, sourceMimeType } from "@/lib/sources";

export const dynamic = "force-dynamic";

// This endpoint is intentionally public: it exposes only the same public
// source paths and raw URLs that the TVs already download. Its 60-second CDN
// cache collapses six receiver refreshes into one authenticated GitHub lookup.
const receiverLibraryHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "public, s-maxage=60, must-revalidate",
};

export async function GET() {
  try {
    const sources = await listSources();
    const files = sources
      .filter((source) => source.kind === "file")
      // A slideshow recipe is resolved only after its parent PowerPoint is
      // chosen. It is not an independently playable TV source.
      .filter((source) => !isSlideShowRecipePath(source.path))
      .map((source) => ({
        path: source.path,
        sha: source.sha,
        size: source.size,
        mimeType: sourceMimeType(source.path),
      }));
    const folders = sources
      .filter((source) => source.kind === "folder")
      .map((source) => ({ path: source.path, sha: source.sha }));
    const revision = createHash("sha256")
      .update(JSON.stringify({ files, folders }))
      .digest("hex");
    return NextResponse.json({ revision, files, folders }, { headers: receiverLibraryHeaders });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not read the public source library." },
      { status: 502, headers: receiverLibraryHeaders },
    );
  }
}
