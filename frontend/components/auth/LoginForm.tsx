"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ErrorAlert } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { safeRedirectTarget } from "@/lib/redirect";

import styles from "./AuthSlider.module.css";

export function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setError(null);
    setSubmitting(true);
    try {
      await login(String(form.get("email")), String(form.get("password")));
      router.replace(safeRedirectTarget(new URLSearchParams(window.location.search).get("next")));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1>Sign in</h1>
      <span>Welcome back to ResearchFlow</span>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <label htmlFor="login-email" className="sr-only">
        Email
      </label>
      <input id="login-email" name="email" type="email" placeholder="Email" autoComplete="email" required />
      <label htmlFor="login-password" className="sr-only">
        Password
      </label>
      <input
        id="login-password"
        name="password"
        type="password"
        placeholder="Password"
        autoComplete="current-password"
        required
      />
      <button type="submit" disabled={submitting}>
        {submitting ? "Please wait…" : "Sign in"}
      </button>
      <p className={styles.switchLink}>
        No account? <Link href="/register">Create one</Link>
      </p>
    </form>
  );
}
