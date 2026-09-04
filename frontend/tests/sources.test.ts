import { describe, expect, it } from "vitest";

import { assertSourceFilename, assertSourceSize, sourcePath, SOURCE_MAX_BYTES } from "@/lib/sources";

describe("source validation", () => {
  it("accepts a flat supported media filename", () => {
    expect(assertSourceFilename("Demo Video 01.mp4")).toBe("Demo Video 01.mp4");
    expect(sourcePath("Demo Video 01.mp4")).toBe("sources/Demo Video 01.mp4");
  });

  it("rejects paths, hidden files, and unplayable source types", () => {
    expect(() => assertSourceFilename("../secret.mp4")).toThrow();
    expect(() => assertSourceFilename("folder/demo.mp4")).toThrow();
    expect(() => assertSourceFilename(".env")).toThrow();
    expect(() => assertSourceFilename("script.exe")).toThrow();
  });

  it("enforces the GitHub-compatible source size limit", () => {
    expect(assertSourceSize(SOURCE_MAX_BYTES)).toBe(SOURCE_MAX_BYTES);
    expect(() => assertSourceSize(SOURCE_MAX_BYTES + 1)).toThrow();
    expect(() => assertSourceSize(0)).toThrow();
  });
});
