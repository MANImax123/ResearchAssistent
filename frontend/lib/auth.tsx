"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { authApi, tokenStore, UNAUTHORIZED_EVENT, type User } from "@/lib/api";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

type AuthContextValue = {
  user: User | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; name: string }) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const signedOut = useCallback(() => {
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  // Restore the session from a stored token (this is what keeps you logged in on refresh)
  useEffect(() => {
    if (!tokenStore.get()) {
      signedOut();
      return;
    }
    authApi
      .me()
      .then((u) => {
        setUser(u);
        setStatus("authenticated");
      })
      .catch(() => {
        tokenStore.clear();
        signedOut();
      });
  }, [signedOut]);

  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, signedOut);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, signedOut);
  }, [signedOut]);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await authApi.login(email, password);
    tokenStore.set(access_token);
    try {
      setUser(await authApi.me());
      setStatus("authenticated");
    } catch (err) {
      tokenStore.clear();
      throw err;
    }
  }, []);

  const register = useCallback(
    async (data: { email: string; password: string; name: string }) => {
      await authApi.register(data);
      await login(data.email, data.password);
    },
    [login],
  );

  const logout = useCallback(() => {
    tokenStore.clear();
    signedOut();
  }, [signedOut]);

  const value = useMemo(() => ({ user, status, login, register, logout }), [user, status, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
