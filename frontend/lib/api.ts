/**
 * Thin fetch wrapper for the ResearchFlow API.
 * Attaches the bearer token, parses JSON, and turns error responses into ApiError.
 */

import type { StageName, StageStatus } from "@/lib/stages";

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

  if (resp.status === 401 && token) handleExpiredToken();
  if (!resp.ok) {
    const data: unknown = await resp.json().catch(() => null);
    throw new ApiError(resp.status, errorMessage(data, resp.status));
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** Stored token expired or was revoked: drop it and let the app send the user to login. */
function handleExpiredToken(): void {
  tokenStore.clear();
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

function errorMessage(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{loc, msg, ...}]
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => {
        const field = e.loc?.at(-1);
        return field && field !== "body" ? `${String(field)}: ${e.msg}` : e.msg;
      })
      .join("; ");
  }
  if (status === 413) return "File is too large";
  return `Request failed (${status})`;
}

/** Multipart POST with upload progress (fetch can't report it). */
export function apiUpload<T>(path: string, data: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}${path}`);
    const token = tokenStore.get();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onerror = () => reject(new ApiError(0, "Cannot reach the server. Is the backend running?"));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON body */
      }
      if (xhr.status === 401 && token) handleExpiredToken();
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(xhr.status, errorMessage(body, xhr.status)));
    };
    xhr.send(data);
  });
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

// ─── Projects ─────────────────────────────────────────────────────────

export type MemberRole = "owner" | "editor" | "viewer";

export type ProjectSummary = {
  id: string;
  title: string;
  domain: string | null;
  target_venue: string | null;
  current_stage: StageName;
  status: "active" | "archived" | "completed";
  role: MemberRole;
  created_at: string;
  updated_at: string;
};

export type ProjectDetail = ProjectSummary & {
  citation_style: string;
  raw_idea: string | null;
  stages: { name: StageName; status: StageStatus; approved_at: string | null }[];
};

export type ProjectInput = {
  title: string;
  domain: string | null;
  raw_idea: string | null;
  target_venue: string | null;
};

export const projectsApi = {
  list: () => apiFetch<ProjectSummary[]>("/projects"),
  get: (id: string) => apiFetch<ProjectDetail>(`/projects/${encodeURIComponent(id)}`),
  create: (data: ProjectInput) => apiFetch<ProjectDetail>("/projects", { method: "POST", json: data }),
  update: (id: string, data: Partial<ProjectInput>) =>
    apiFetch<ProjectDetail>(`/projects/${encodeURIComponent(id)}`, { method: "PATCH", json: data }),
  remove: (id: string) => apiFetch<void>(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

// ─── Files ────────────────────────────────────────────────────────────

export type FileKind = "paper_pdf" | "dataset" | "diagram" | "code" | "notes" | "result";

export type ProjectFile = {
  id: string;
  kind: FileKind;
  filename: string;
  mime: string;
  size: number;
  uploaded_by: string | null;
  created_at: string;
};

const filesPath = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/files`;

export const filesApi = {
  list: (projectId: string) => apiFetch<ProjectFile[]>(filesPath(projectId)),
  upload: (projectId: string, file: File, kind: FileKind, onProgress?: (fraction: number) => void) => {
    const data = new FormData();
    data.append("kind", kind);
    data.append("file", file);
    return apiUpload<ProjectFile>(filesPath(projectId), data, onProgress);
  },
  downloadUrl: (projectId: string, fileId: string) =>
    apiFetch<{ url: string; expires_in: number }>(`${filesPath(projectId)}/${encodeURIComponent(fileId)}/download`),
  remove: (projectId: string, fileId: string) =>
    apiFetch<void>(`${filesPath(projectId)}/${encodeURIComponent(fileId)}`, { method: "DELETE" }),
};
