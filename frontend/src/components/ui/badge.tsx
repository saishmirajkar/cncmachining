import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-border bg-surface-2 text-muted",
        accent: "border-accent/25 bg-accent-soft text-accent",
        ok: "border-ok/25 bg-ok-soft text-ok",
        warn: "border-warn/30 bg-warn-soft text-warn",
        err: "border-err/25 bg-err-soft text-err",
        info: "border-info/20 bg-info-soft text-info",
        solid: "border-transparent bg-foreground text-background",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...p }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...p} />;
}
