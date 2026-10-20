/**
 * Thin fetch wrapper for the ResearchFlow API.
 * Attaches the bearer token, parses JSON, and turns error responses into ApiError.
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "researchflow.token";
export const UNAUTHORIZED_EVENT = "researchflow:unauthorized";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// localStorage can be unavailable (private mode, SSR) — treat that as "no token"
export const tokenStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* session then lasts only for this page load */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* nothing stored */
    }
  },
};

type RequestOptions = Omit<RequestInit, "body"> & {
  json?: unknown;
  form?: Record<string, string>;
};

export async function apiFetch<T>(path: string, { json, form, headers, ...init }: RequestOptions = {}): Promise<T> {
  const finalHeaders = new Headers(headers);
  let body: BodyInit | undefined;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = new URLSearchParams(form);
  }
  const token = tokenStore.get();
  if (token) finalHeaders.set("Authorization", `Bearer ${token}`);

  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, { ...init, headers: finalHeaders, body });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the backend running?");
  }

  if (resp.status === 401 && token) {
    // Stored token expired or was revoked: drop it and let the app send the user to login
    tokenStore.clear();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (!resp.ok) throw new ApiError(resp.status, await errorMessage(resp));
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

async function errorMessage(resp: Response): Promise<string> {
  try {
    const data = (await resp.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    // FastAPI validation errors: [{loc, msg, ...}]
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((e: { loc?: unknown[]; msg?: string }) => {
          const field = e.loc?.at(-1);
          return field && field !== "body" ? `${String(field)}: ${e.msg}` : e.msg;
        })
        .join("; ");
    }
  } catch {
    /* non-JSON body */
  }
  return `Request failed (${resp.status})`;
}

// ─── Auth endpoints ───────────────────────────────────────────────────

export type User = { id: string; email: string; name: string; created_at: string };
type TokenResponse = { access_token: string; token_type: "bearer"; expires_in: number };

export const authApi = {
  login: (email: string, password: string) =>
    apiFetch<TokenResponse>("/auth/login", { method: "POST", form: { username: email, password } }),
  register: (data: { email: string; password: string; name: string }) =>
    apiFetch<User>("/auth/register", { method: "POST", json: data }),
  me: () => apiFetch<User>("/auth/me"),
};
