"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { AuthSlider } from "@/components/auth/AuthSlider";
import styles from "@/components/auth/AuthSlider.module.css";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

// The slider lives here, not in the pages: this layout stays mounted when moving
// between /login and /register, so switching animates instead of remounting.
export default function AuthLayout({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // Already signed in (e.g. after a refresh on /login) → go straight to the app
  useEffect(() => {
    if (status === "authenticated") router.replace("/dashboard");
  }, [status, router]);

  return (
    <main className={`${styles.page} flex min-h-dvh flex-col items-center justify-center px-4 py-12`}>
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap"
        precedence="default"
      />
      <p className={`${styles.brand} mb-6 text-center text-2xl font-semibold tracking-tight`}>ResearchFlow</p>
      {status === "unauthenticated" ? (
        <AuthSlider mode={pathname === "/register" ? "register" : "login"} />
      ) : (
        <Spinner />
      )}
      {children}
    </main>
  );
}
