import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { savePictureInPictureRecipe } from "@/lib/github";
import { listReceiverStatuses, stageSourceForReceivers } from "@/lib/receivers";
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
    const input = await request.json() as {
      kind?: unknown;
      receiverId?: unknown;
      receiverIds?: unknown;
      sourcePath?: unknown;
      baseSourcePath?: unknown;
      overlaySourcePath?: unknown;
      layout?: unknown;
      removeBackground?: unknown;
    };
    const receiverIds = Array.isArray(input.receiverIds)
      ? input.receiverIds.map((receiverId) => String(receiverId || ""))
      : [String(input.receiverId || "")];
    if (input.kind === "save-picture-in-picture") {
      const recipe = await savePictureInPictureRecipe({
        baseSourcePath: String(input.baseSourcePath || ""),
        overlaySourcePath: String(input.overlaySourcePath || ""),
        layout: input.layout,
        removeBackground: input.removeBackground,
      });
      const commands = await stageSourceForReceivers({ receiverIds, sourcePath: recipe.path });
      return NextResponse.json({ commands, recipe }, { status: 201 });
    }
    const commands = await stageSourceForReceivers({ receiverIds, sourcePath: String(input.sourcePath || "") });
    return NextResponse.json({ commands }, { status: 201 });
  } catch (error) {
    const status = error instanceof SourceValidationError ? 400 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not stage the source." }, { status });
  }
}
