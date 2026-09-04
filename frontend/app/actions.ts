"use server";

import { redirect } from "next/navigation";

import { clearSession, createSession, passwordMatches } from "@/lib/auth";

export async function login(formData: FormData): Promise<void> {
  const password = String(formData.get("password") || "");
  if (!passwordMatches(password)) {
    redirect("/login?error=invalid-password");
  }
  await createSession();
  redirect("/");
}

export async function logout(): Promise<void> {
  await clearSession();
  redirect("/login");
}
