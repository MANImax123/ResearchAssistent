"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button, ErrorAlert } from "@/components/ui";

type Props = {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
};

/** Modal confirmation for destructive actions. Escape or the backdrop cancels. */
export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus(); // safe default for a destructive dialog
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={() => !busy && onClose()} aria-hidden />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className="relative w-full max-w-md space-y-4 rounded-xl bg-white p-6 shadow-xl"
      >
        <h2 id="confirm-title" className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        <div className="text-sm text-slate-600">{children}</div>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <div className="flex justify-end gap-3">
          <Button ref={cancelRef} type="button" variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" variant="danger" onClick={confirm} loading={busy}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
