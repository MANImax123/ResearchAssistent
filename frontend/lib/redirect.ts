const DEFAULT_TARGET = "/dashboard";

/** Only allow same-site paths as post-login redirects (blocks `//evil.com`, `https://…`). */
export function safeRedirectTarget(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return DEFAULT_TARGET;
  return next;
}
