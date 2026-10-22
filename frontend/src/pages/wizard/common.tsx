import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { SeverityIcon } from "@/components/status";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Requirement, Setup } from "@/lib/types";
import { STEPS, type StepKey } from "./Wizard";

export function usePatchSetup(setupId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Setup>(`/api/setups/${setupId}`, { method: "PATCH", json: body }),
    onSuccess: (s) => {
      qc.setQueryData(["setup", setupId], s);
      qc.invalidateQueries({ queryKey: ["jobs", "setup", setupId] });
    },
  });
}

export function StepNav({ setup, current, nextDisabled, nextLabel }: { setup: Setup; current: StepKey; nextDisabled?: boolean; nextLabel?: string }) {
  const i = STEPS.findIndex((s) => s.key === current);
  const prev = STEPS[i - 1];
  const next = STEPS[i + 1];
  return (
    <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
      {prev ? (
        <Button variant="ghost" asChild>
          <Link to={`/jobs/${setup.id}/${prev.key}`}>
            <ArrowLeft /> {prev.label}
          </Link>
        </Button>
      ) : (
        <span />
      )}
      {next &&
        (nextDisabled ? (
          <Button disabled>
            {nextLabel ?? next.label} <ArrowRight />
          </Button>
        ) : (
          <Button asChild data-testid="next-step">
            <Link to={`/jobs/${setup.id}/${next.key}`}>
              {nextLabel ?? next.label} <ArrowRight />
            </Link>
          </Button>
        ))}
    </div>
  );
}

export function RequirementList({ items, fields }: { items: Requirement[]; fields?: string[] }) {
  const shown = fields ? items.filter((r) => fields.some((f) => r.field === f || r.field.startsWith(f + "."))) : items;
  if (!shown.length) return null;
  return (
    <ul className="space-y-1.5" data-testid="requirements">
      {shown.map((r, i) => (
        <li key={i} className="flex gap-2 text-sm">
          <SeverityIcon severity={r.severity} className="mt-0.5 size-4 shrink-0" />
          <span>
            <span className="font-mono text-xs text-muted">{r.field}</span> — {r.message}
          </span>
        </li>
      ))}
    </ul>
  );
}
