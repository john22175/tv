import "server-only";

import { timingSafeEqual } from "node:crypto";
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

const SESSION_COOKIE = "tv-source-dashboard";
const SESSION_DURATION_SECONDS = 60 * 60 * 12;

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is not configured.`);
  }
  return value;
}

function sessionSecret(): Uint8Array {
  return new TextEncoder().encode(requiredEnvironment("SESSION_SECRET"));
}

export function passwordMatches(candidate: string): boolean {
  const expected = Buffer.from(requiredEnvironment("SOURCE_DASHBOARD_PASSWORD"));
  const supplied = Buffer.from(candidate);
  return expected.length === supplied.length && timingSafeEqual(expected, supplied);
}

export async function createSession(): Promise<void> {
  const token = await new SignJWT({ role: "source-manager" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_DURATION_SECONDS}s`)
    .sign(sessionSecret());

  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_DURATION_SECONDS,
  });
}

export async function clearSession(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
}

export async function isAuthenticated(): Promise<boolean> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return false;
  }
  try {
    const { payload } = await jwtVerify(token, sessionSecret());
    return payload.role === "source-manager";
  } catch {
    return false;
  }
}
