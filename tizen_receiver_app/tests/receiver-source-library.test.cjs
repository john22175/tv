const assert = require("node:assert/strict");
const test = require("node:test");

require("../app/js/source-library.js");

const library = globalThis.MultiHubSourceLibrary;

test("only sources directory blobs are published to the receiver", () => {
  assert.equal(library.isGitHubSourcePath("sources/demo.mp4"), true);
  assert.equal(library.isGitHubSourcePath("sources/room-a/demo.mp4"), true);
  assert.equal(library.isGitHubSourcePath("sources/.private.mp4"), false);
  assert.equal(library.isGitHubSourcePath("multihub/main_window.py"), false);
  assert.equal(library.isGitHubSourcePath("frontend/app/page.tsx"), false);
  assert.equal(library.isGitHubSourcePath("tizen_receiver_app/app/js/app.js"), false);
  assert.equal(library.githubSourceRelativePath("sources/room-a/demo.mp4"), "room-a/demo.mp4");
});
