(function attachReceiverControl(global) {
  "use strict";

  // This is the stable Vercel production alias for the source dashboard.
  // It exposes only public, per-TV staging commands; editing remains behind
  // the dashboard password on the server.
  global.MultiHubReceiverControl = {
    baseUrl: "https://tv-sepia-seven.vercel.app",
    pollIntervalMs: 30000,
  };
}(globalThis));
