(function attachSourceLibrary(global) {
  "use strict";

  const GITHUB_SOURCES_PREFIX = "sources/";

  function isGitHubSourcePath(path) {
    const value = String(path || "");
    if (!value.startsWith(GITHUB_SOURCES_PREFIX)) {
      return false;
    }
    const relativePath = value.slice(GITHUB_SOURCES_PREFIX.length);
    // The published library is deliberately flat.  Accepting subdirectories
    // here would make it possible for a recursive GitHub tree query to expose
    // files that are not shown by the repository's sources/ directory view.
    return Boolean(relativePath)
      && !relativePath.includes("/")
      && !relativePath.startsWith(".");
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
