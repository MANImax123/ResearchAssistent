import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/LoginForm";
import { tokenStore } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { safeRedirectTarget } from "@/lib/redirect";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderLogin() {
  return render(
    <AuthProvider>
      <LoginForm />
    </AuthProvider>,
  );
}

describe("LoginForm", () => {
  beforeEach(() => {
    replace.mockReset();
    vi.unstubAllGlobals();
  });

  it("renders email, password and submit", () => {
    renderLogin();

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Create one" })).toHaveAttribute("href", "/register");
  });

  it("stores the token and goes to the dashboard on success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(200, { access_token: "tok123", token_type: "bearer", expires_in: 60 }))
      .mockResolvedValueOnce(json(200, { id: "u1", email: "a@b.co", name: "A", created_at: "" }));
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "password1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(tokenStore.get()).toBe("tok123");
    const [loginUrl, loginInit] = fetchMock.mock.calls[0];
    expect(loginUrl).toMatch(/\/auth\/login$/);
    expect(String(loginInit.body)).toBe("username=a%40b.co&password=password1");
    expect(fetchMock.mock.calls[1][1].headers.get("Authorization")).toBe("Bearer tok123");
  });

  it("shows the server's error on wrong credentials", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { detail: "Incorrect email or password" })));
    renderLogin();

    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
    expect(replace).not.toHaveBeenCalled();
    expect(tokenStore.get()).toBeNull();
  });
});

describe("safeRedirectTarget", () => {
  it.each([
    ["/dashboard/x", "/dashboard/x"],
    [null, "/dashboard"],
    ["https://evil.com", "/dashboard"],
    ["//evil.com", "/dashboard"],
    ["/\\evil.com", "/dashboard"],
  ])("%s → %s", (input, expected) => {
    expect(safeRedirectTarget(input)).toBe(expected);
  });
});
