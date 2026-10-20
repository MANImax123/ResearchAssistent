"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button, ErrorAlert, TextField } from "@/components/ui";
import { useAuth } from "@/lib/auth";

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
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Create your account</h1>
        <p className="mt-1 text-sm text-slate-500">Start your first research project.</p>
      </div>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <TextField label="Full name" id="name" name="name" autoComplete="name" required />
      <TextField label="Email" id="email" name="email" type="email" autoComplete="email" required />
      <TextField
        label="Password"
        id="password"
        name="password"
        type="password"
        autoComplete="new-password"
        hint={`At least ${MIN_PASSWORD} characters.`}
        required
      />
      <TextField
        label="Confirm password"
        id="confirm"
        name="confirm"
        type="password"
        autoComplete="new-password"
        required
      />
      <Button type="submit" loading={submitting} className="w-full">
        Create account
      </Button>
      <p className="text-center text-sm text-slate-500">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-indigo-600 hover:text-indigo-500">
          Sign in
        </Link>
      </p>
    </form>
  );
}
