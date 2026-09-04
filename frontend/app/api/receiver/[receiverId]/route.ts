import { NextRequest, NextResponse } from "next/server";

import { getReceiverStage } from "@/lib/receivers";
import { SourceValidationError } from "@/lib/sources";

export const dynamic = "force-dynamic";

const receiverHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "no-store, max-age=0",
};

export async function GET(_request: NextRequest, context: { params: Promise<{ receiverId: string }> }) {
  try {
    const { receiverId } = await context.params;
    return NextResponse.json({ command: await getReceiverStage(receiverId) }, { headers: receiverHeaders });
  } catch (error) {
    const status = error instanceof SourceValidationError ? 404 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not read receiver command." }, { status, headers: receiverHeaders });
  }
}
