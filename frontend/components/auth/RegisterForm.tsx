"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ErrorAlert } from "@/components/ui";
import { useAuth } from "@/lib/auth";

import styles from "./AuthSlider.module.css";

const MIN_PASSWORD = 8;

export function RegisterForm() {
  const { register } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const password = String(form.get("password"));
    if (password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (password !== form.get("confirm")) {
      setError("Passwords do not match.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await register({ name: String(form.get("name")), email: String(form.get("email")), password });
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1>Create account</h1>
      <span>Start your first research project</span>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <label htmlFor="register-name" className="sr-only">
        Full name
      </label>
      <input id="register-name" name="name" placeholder="Full name" autoComplete="name" required />
      <label htmlFor="register-email" className="sr-only">
        Email
      </label>
      <input id="register-email" name="email" type="email" placeholder="Email" autoComplete="email" required />
      <label htmlFor="register-password" className="sr-only">
        Password
      </label>
      <input
        id="register-password"
        name="password"
        type="password"
        placeholder={`Password (at least ${MIN_PASSWORD} characters)`}
        autoComplete="new-password"
        required
      />
      <label htmlFor="register-confirm" className="sr-only">
        Confirm password
      </label>
      <input
        id="register-confirm"
        name="confirm"
        type="password"
        placeholder="Confirm password"
        autoComplete="new-password"
        required
      />
      <button type="submit" disabled={submitting}>
        {submitting ? "Please wait…" : "Sign up"}
      </button>
      <p className={styles.switchLink}>
        Already have an account? <Link href="/login">Sign in</Link>
      </p>
    </form>
  );
}
