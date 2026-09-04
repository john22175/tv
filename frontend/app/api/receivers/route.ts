import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { listReceiverStatuses, stageSourceForReceiver } from "@/lib/receivers";
import { SourceValidationError } from "@/lib/sources";

export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "Authentication required." }, { status: 401 });
}

export async function GET() {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    return NextResponse.json({ receivers: await listReceiverStatuses() });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not load receiver status." }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  if (!(await isAuthenticated())) return unauthorized();
  try {
    const input = await request.json() as { receiverId?: unknown; sourcePath?: unknown };
    const command = await stageSourceForReceiver({ receiverId: String(input.receiverId || ""), sourcePath: String(input.sourcePath || "") });
    return NextResponse.json({ command }, { status: 201 });
  } catch (error) {
    const status = error instanceof SourceValidationError ? 400 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not stage the source." }, { status });
  }
}
