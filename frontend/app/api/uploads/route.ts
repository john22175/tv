import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { NextRequest, NextResponse } from "next/server";

import { isAuthenticated } from "@/lib/auth";
import { dispatchSourcePublish, sourceExists } from "@/lib/github";
import { assertSourceFilename, SOURCE_MAX_BYTES, SourceValidationError } from "@/lib/sources";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type UploadPayload = { filename: string; requestId: string };

function parsePayload(value: string | null | undefined): UploadPayload {
  try {
    const parsed = JSON.parse(value || "{}") as Partial<UploadPayload>;
    const filename = assertSourceFilename(parsed.filename);
    if (!/^[a-zA-Z0-9_-]{8,64}$/.test(String(parsed.requestId || ""))) {
      throw new SourceValidationError("Upload request identifier is invalid.");
    }
    return { filename, requestId: String(parsed.requestId) };
  } catch (error) {
    if (error instanceof SourceValidationError) {
      throw error;
    }
    throw new SourceValidationError("Upload details are invalid.");
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const body = await request.json() as HandleUploadBody;
  if (body.type === "blob.generate-client-token" && !(await isAuthenticated())) {
    return NextResponse.json({ error: "Authentication required." }, { status: 401 });
  }

  try {
    const response = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname, clientPayload) => {
        const payload = parsePayload(clientPayload);
        if (pathname !== `pending/${payload.requestId}/${payload.filename}`) {
          throw new SourceValidationError("Upload path does not match the approved source.");
        }
        if (await sourceExists(payload.filename)) {
          throw new SourceValidationError("A source with that filename already exists.");
        }
        return {
          allowedContentTypes: ["application/octet-stream", "audio/*", "image/*", "video/*", "application/pdf", "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
          maximumSizeInBytes: SOURCE_MAX_BYTES,
          addRandomSuffix: true,
          tokenPayload: JSON.stringify(payload),
        };
      },
      onUploadCompleted: async ({ blob, tokenPayload }) => {
        const payload = parsePayload(String(tokenPayload || ""));
        await dispatchSourcePublish({
          filename: payload.filename,
          requestId: payload.requestId,
          uploadUrl: blob.url,
        });
      },
    });
    return NextResponse.json(response);
  } catch (error) {
    const status = error instanceof SourceValidationError ? 400 : 502;
    return NextResponse.json({ error: error instanceof Error ? error.message : "Could not start the upload." }, { status });
  }
}
