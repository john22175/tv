(function attachSourceRepository(global) {
  "use strict";

  // This legacy-safe default keeps existing packages working until a deploy
  // target explicitly selects the separate media-only source repository.
  // deploy-receiver.ps1 writes the configured value into each package.
  global.MultiHubSourceRepository = {
    owner: "john22175",
    repository: "tv",
    branch: "main",
  };
}(globalThis));
