(function attachSourceRepository(global) {
  "use strict";

  // This is the production media repository. deploy-receiver.ps1 writes the
  // same configured value into each receiver-specific package.
  global.MultiHubSourceRepository = {
    owner: "john22175",
    repository: "t-sources",
    branch: "main",
  };
}(globalThis));
