import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { createSourceFolder, deleteSource, deleteSourceFolder, listSources, moveSource } from "@/lib/github";
import { SourceValidationError } from "@/lib/sources";

export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "Authentication required." }, { status: 401 });
}

function failure(error: unknown, fallback: string) {
  const status = error instanceof SourceValidationError ? 400 : 502;
  return NextResponse.json({ error: error instanceof Error ? error.message : fallback }, { status });
}

export async function GET() {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    return NextResponse.json({ sources: await listSources() });
  } catch (error) {
    return failure(error, "Could not load sources.");
  }
}

export async function POST(request: NextRequest) {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    const input = await request.json() as { path?: unknown };
    await createSourceFolder({ path: input.path as string });
    return NextResponse.json({ ok: true }, { status: 201 });
  } catch (error) {
    return failure(error, "Could not create the folder.");
  }
}

export async function PATCH(request: NextRequest) {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    const input = await request.json() as { fromPath?: unknown; toPath?: unknown; sha?: unknown };
    await moveSource({ fromPath: input.fromPath as string, toPath: input.toPath as string, sha: String(input.sha || "") });
    return NextResponse.json({ ok: true });
  } catch (error) {
    return failure(error, "Could not move the source.");
  }
}

export async function DELETE(request: NextRequest) {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    const input = await request.json() as { kind?: unknown; path?: unknown; sha?: unknown };
    if (input.kind === "folder") {
      await deleteSourceFolder({ path: input.path as string, markerSha: String(input.sha || "") });
    } else {
      await deleteSource({ path: input.path as string, sha: String(input.sha || "") });
    }
    return NextResponse.json({ ok: true });
  } catch (error) {
    return failure(error, "Could not delete the source.");
  }
}
