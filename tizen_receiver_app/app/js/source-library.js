(function attachSourceLibrary(global) {
  "use strict";

  const GITHUB_SOURCES_PREFIX = "sources/";

  function isGitHubSourcePath(path) {
    const value = String(path || "");
    if (!value.startsWith(GITHUB_SOURCES_PREFIX)) {
      return false;
    }
    const relativePath = value.slice(GITHUB_SOURCES_PREFIX.length);
    // The caller supplies paths rooted at sources/. Every component must be
    // visible; a recursive query of the sources subtree never sees repo code.
    return Boolean(relativePath)
      && !relativePath.split("/").some((part) => !part || part.startsWith("."));
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
