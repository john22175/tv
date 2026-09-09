import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { createGitHubInstallationToken } from "@/lib/github-app";
import { sourceExists } from "@/lib/github";
import { assertSourcePath, assertSourceSize, sourcePath, SourceValidationError } from "@/lib/sources";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type UploadRequest = { path?: unknown; size?: unknown };

function sourceRepository() {
  const owner = process.env.SOURCE_GITHUB_OWNER?.trim() || process.env.GITHUB_OWNER?.trim();
  const repository = process.env.SOURCE_GITHUB_REPOSITORY?.trim() || process.env.GITHUB_REPOSITORY?.trim();
  const branch = process.env.SOURCE_GITHUB_BRANCH?.trim() || process.env.GITHUB_BRANCH?.trim() || "main";
  if (!owner || !repository) {
    throw new Error("SOURCE_GITHUB_OWNER and SOURCE_GITHUB_REPOSITORY must be configured.");
  }
  return { owner, repository, branch };
}

function encodedPath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/**
 * Returns a short-lived GitHub App installation token for one direct browser
 * upload. The token is never stored in the browser and the app must be
 * installed only on the separate media-only source repository.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Authentication required." }, { status: 401 });
  }
  try {
    const input = await request.json() as UploadRequest;
    const path = assertSourcePath(input.path);
    assertSourceSize(input.size);
    if (await sourceExists(path)) {
      throw new SourceValidationError("A source with that filename already exists.");
    }
    const { owner, repository, branch } = sourceRepository();
    const { token, expires_at: expiresAt } = await createGitHubInstallationToken("GITHUB_SOURCE_UPLOAD");
    return NextResponse.json({
      contentUrl: `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${encodedPath(sourcePath(path))}`,
      branch,
      path,
      token,
      expiresAt,
    }, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const status = error instanceof SourceValidationError ? 400 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not authorize the upload." }, { status });
  }
}
