import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthSlider } from "@/components/auth/AuthSlider";
import { AuthProvider } from "@/lib/auth";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push }) }));

function renderSlider(mode: "login" | "register") {
  return render(
    <AuthProvider>
      <AuthSlider mode={mode} />
    </AuthProvider>,
  );
}

describe("AuthSlider", () => {
  beforeEach(() => push.mockReset());

  it("shows the sign-in form and makes the register form inert in login mode", () => {
    renderSlider("login");

    const signIn = screen.getByRole("heading", { name: "Sign in" }).closest("form")!.parentElement!;
    const signUp = screen.getByRole("heading", { name: "Create account" }).closest("form")!.parentElement!;
    expect(signIn).not.toHaveAttribute("inert");
    expect(signUp).toHaveAttribute("inert");
  });

  it("makes the sign-in form inert in register mode", () => {
    renderSlider("register");

    const signIn = screen.getByRole("heading", { name: "Sign in" }).closest("form")!.parentElement!;
    const signUp = screen.getByRole("heading", { name: "Create account" }).closest("form")!.parentElement!;
    expect(signIn).toHaveAttribute("inert");
    expect(signUp).not.toHaveAttribute("inert");
  });

  it("navigates when the overlay buttons are clicked", async () => {
    renderSlider("login");

    await userEvent.click(screen.getByRole("button", { name: "Sign up instead" }));
    expect(push).toHaveBeenCalledWith("/register");

    await userEvent.click(screen.getByRole("button", { name: "Sign in instead" }));
    expect(push).toHaveBeenCalledWith("/login");
  });
});
