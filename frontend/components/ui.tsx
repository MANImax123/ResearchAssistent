import Link from "next/link";
import type {
  ComponentProps,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

type Variant = "primary" | "secondary" | "danger" | "danger-outline";

const BUTTON_BASE =
  "inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60";
const BUTTON_VARIANTS: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500 focus-visible:outline-indigo-600",
  secondary:
    "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 focus-visible:outline-indigo-600",
  danger: "bg-red-600 text-white hover:bg-red-500 focus-visible:outline-red-600",
  "danger-outline": "border border-red-200 bg-white text-red-600 hover:bg-red-50 focus-visible:outline-red-600",
};

export function Button({
  loading,
  variant = "primary",
  type = "button", // never submit a form by accident; submit buttons say so explicitly
  children,
  className = "",
  ...props
}: ComponentProps<"button"> & { loading?: boolean; variant?: Variant }) {
  return (
    <button
      type={type}
      {...props}
      disabled={props.disabled || loading}
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${className}`}
    >
      {loading ? "Please wait…" : children}
    </button>
  );
}

export function LinkButton({
  variant = "primary",
  className = "",
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link {...props} className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${className}`} />;
}

const FIELD_INPUT =
  "block w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 shadow-xs placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function TextField({
  label,
  id,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; id: string; hint?: string }) {
  return (
    <Field id={id} label={label} hint={hint}>
      <input id={id} {...props} className={`h-10 ${FIELD_INPUT}`} />
    </Field>
  );
}

export function TextArea({
  label,
  id,
  hint,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; id: string; hint?: string }) {
  return (
    <Field id={id} label={label} hint={hint}>
      <textarea id={id} rows={5} {...props} className={`py-2 ${FIELD_INPUT}`} />
    </Field>
  );
}

export function Select({
  label,
  id,
  hint,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; id: string; hint?: string }) {
  return (
    <Field id={id} label={label} hint={hint}>
      <select id={id} {...props} className={`h-10 ${FIELD_INPUT}`}>
        {children}
      </select>
    </Field>
  );
}

export function ErrorAlert({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
    >
      <span>{children}</span>
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-sm text-slate-500">
      <span className="size-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
      {label}…
    </div>
  );
}

export function Badge({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${className}`}>
      {children}
    </span>
  );
}
