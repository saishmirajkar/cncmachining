import * as LabelPrimitive from "@radix-ui/react-label";
import * as React from "react";
import { cn } from "@/lib/utils";

const fieldBase =
  "w-full rounded-md border border-border-strong bg-surface px-2.5 text-sm shadow-[inset_0_1px_1px_rgba(15,23,42,0.03)] placeholder:text-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:border-accent disabled:opacity-60 disabled:bg-surface-2";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => <input ref={ref} className={cn(fieldBase, "h-9", className)} {...p} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => <textarea ref={ref} className={cn(fieldBase, "min-h-20 py-2", className)} {...p} />,
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...p }, ref) => (
    <select ref={ref} className={cn(fieldBase, "h-9 pr-8", className)} {...p}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export function Label({ className, ...p }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("text-xs font-medium text-foreground/80", className)} {...p} />;
}

/** Labelled field with optional hint, unit suffix and error. */
export function Field({
  label,
  hint,
  error,
  unit,
  required,
  children,
  className,
}: {
  label: string;
  hint?: React.ReactNode;
  error?: string | null;
  unit?: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const autoId = React.useId();
  // bind the label to the control (accessibility and label-based queries)
  const child = React.isValidElement<{ id?: string }>(children) ? children : null;
  const id = child?.props.id ?? autoId;
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="text-err"> *</span>}
      </Label>
      <div className="relative">
        {child ? React.cloneElement(child, { id }) : children}
        {unit && (
          <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-muted">{unit}</span>
        )}
      </div>
      {error ? <p className="text-xs text-err">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

/** Numeric input that keeps an empty string as null (never silently substitutes a default). */
export function NumberInput({
  value,
  onChange,
  step = "any",
  ...p
}: Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
}) {
  return (
    <Input
      type="number"
      inputMode="decimal"
      step={step}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      className="pr-12 tabular"
      {...p}
    />
  );
}
