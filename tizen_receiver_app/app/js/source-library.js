(function attachSourceLibrary(global) {
  "use strict";

  const GITHUB_SOURCES_PREFIX = "sources/";

  function isGitHubSourcePath(path) {
    const value = String(path || "");
    if (!value.startsWith(GITHUB_SOURCES_PREFIX)) {
      return false;
    }
    const relativePath = value.slice(GITHUB_SOURCES_PREFIX.length);
    return Boolean(relativePath) && !relativePath.split("/").some((part) => !part || part.startsWith("."));
  }

  function githubSourceRelativePath(path) {
    return String(path || "").slice(GITHUB_SOURCES_PREFIX.length);
  }

  global.MultiHubSourceLibrary = {
    GITHUB_SOURCES_PREFIX,
    isGitHubSourcePath,
    githubSourceRelativePath,
  };
}(globalThis));
