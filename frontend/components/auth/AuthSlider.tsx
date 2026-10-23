"use client";

import { useRouter } from "next/navigation";

import { LoginForm } from "./LoginForm";
import { RegisterForm } from "./RegisterForm";
import styles from "./AuthSlider.module.css";

export type AuthMode = "login" | "register";

/**
 * Both forms side by side with a sliding overlay. The mode comes from the URL, so
 * the overlay buttons just navigate; the (auth) layout stays mounted and animates.
 */
export function AuthSlider({ mode }: { mode: AuthMode }) {
  const router = useRouter();
  const registering = mode === "register";

  return (
    <div className={`${styles.container} ${registering ? styles.active : ""}`} data-testid="auth-slider">
      {/* The hidden form is inert so Tab and screen readers skip it */}
      <div className={`${styles.formContainer} ${styles.signUp}`} inert={!registering}>
        <RegisterForm />
      </div>
      <div className={`${styles.formContainer} ${styles.signIn}`} inert={registering}>
        <LoginForm />
      </div>
      <div className={styles.toggleContainer}>
        <div className={styles.toggle}>
          <div className={`${styles.togglePanel} ${styles.toggleLeft}`} inert={!registering}>
            <h1>Welcome back!</h1>
            <p>Already have an account? Sign in to pick up your research where you left off.</p>
            <button type="button" className={styles.ghost} onClick={() => router.push("/login")}>
              Sign in instead
            </button>
          </div>
          <div className={`${styles.togglePanel} ${styles.toggleRight}`} inert={registering}>
            <h1>Hello, researcher!</h1>
            <p>New to ResearchFlow? Create an account to start your first research project.</p>
            <button type="button" className={styles.ghost} onClick={() => router.push("/register")}>
              Sign up instead
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
