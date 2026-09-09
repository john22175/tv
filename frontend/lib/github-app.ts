import "server-only";

import { createPrivateKey } from "node:crypto";
import { importPKCS8, SignJWT } from "jose";

type GitHubInstallationToken = {
  token: string;
  expires_at: string;
};

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is not configured.`);
  return value;
}

/**
 * Exchanges a GitHub App JWT for an installation token. The caller chooses a
 * prefix such as GITHUB_CONTROL or GITHUB_SOURCE_UPLOAD; no token is cached
 * here so callers can decide whether the token may ever leave the server.
 */
export async function createGitHubInstallationToken(prefix: string): Promise<GitHubInstallationToken> {
  const appId = requiredEnvironment(`${prefix}_APP_ID`);
  const installationId = requiredEnvironment(`${prefix}_INSTALLATION_ID`);
  const pem = requiredEnvironment(`${prefix}_PRIVATE_KEY`).replace(/\\n/g, "\n");
  // GitHub downloads App keys as PKCS#1 (BEGIN RSA PRIVATE KEY), while jose's
  // importer expects PKCS#8. Node normalizes either supported PEM form first.
  const pkcs8Pem = createPrivateKey(pem).export({ format: "pem", type: "pkcs8" }).toString();
  const privateKey = await importPKCS8(pkcs8Pem, "RS256");
  const now = Math.floor(Date.now() / 1000);
  const appJwt = await new SignJWT({})
    .setProtectedHeader({ alg: "RS256" })
    .setIssuer(appId)
    .setIssuedAt(now - 30)
    .setExpirationTime(now + 9 * 60)
    .sign(privateKey);

  const response = await fetch(`https://api.github.com/app/installations/${encodeURIComponent(installationId)}/access_tokens`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${appJwt}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "tv-source-dashboard",
    },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`GitHub App token request failed (${response.status}).`);
  }
  const payload = await response.json() as Partial<GitHubInstallationToken>;
  if (!payload.token || !payload.expires_at) {
    throw new Error("GitHub App did not return an installation token.");
  }
  return { token: payload.token, expires_at: payload.expires_at };
}
