import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { deleteSource, listSources } from "@/lib/github";
import { SourceValidationError } from "@/lib/sources";

export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "Authentication required." }, { status: 401 });
}

export async function GET() {
  if (!(await isAuthenticated())) {
    return unauthorized();
  }
  try {
    return NextResponse.json({ sources: await listSources() });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not load sources." }, { status: 502 });
  }
}

export async function DELETE(request: NextRequest) {
  if (!(await isAuthenticated())) {
    return unauthorized();
  }
  try {
    const input = await request.json() as { filename?: unknown; sha?: unknown };
    await deleteSource({ filename: input.filename as string, sha: String(input.sha || "") });
    return NextResponse.json({ ok: true });
  } catch (error) {
    const status = error instanceof SourceValidationError ? 400 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not delete source." }, { status });
  }
}
