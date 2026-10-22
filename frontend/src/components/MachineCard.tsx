import { Cpu, FileCode2, Gauge } from "lucide-react";
import { VerificationBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import type { MachineConfig } from "@/lib/types";
import { cn, fmt } from "@/lib/utils";

export const MILLING = new Set(["vmc", "router"]);

export function MachineCard({ m, selected, onSelect, compact }: { m: MachineConfig; selected?: boolean; onSelect?: () => void; compact?: boolean }) {
  const mm = m.machine_model;
  const lim = m.effective_limits;
  const supported = MILLING.has(mm.category) && mm.axis_count >= 3;
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={!onSelect}
      data-testid={`machine-${m.id}`}
      className={cn(
        "w-full rounded-lg border bg-surface p-4 text-left transition-colors enabled:hover:border-accent/60",
        selected ? "border-accent ring-2 ring-accent/20" : "border-border",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{m.name}</div>
          <div className="truncate text-xs text-muted">
            {mm.manufacturer.name} · {mm.model} · {mm.category.toUpperCase()} · {mm.axis_count} axes
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {m.is_example && <Badge tone="warn">Example</Badge>}
          {m.scope === "organization" && <Badge tone="accent">Organisation</Badge>}
          <VerificationBadge status={m.verification_status} />
        </div>
      </div>
      <div className={cn("mt-3 grid gap-3 text-xs", compact ? "grid-cols-1" : "grid-cols-3")}>
        <div className="flex gap-2">
          <Gauge className="mt-0.5 size-3.5 shrink-0 text-muted" />
          <div>
            <div className="font-medium">Machine</div>
            <div className="text-muted tabular">
              X{fmt(lim.travel_x_mm, 0)} Y{fmt(lim.travel_y_mm, 0)} Z{fmt(lim.travel_z_mm, 0)} mm
              <br />
              {fmt(lim.spindle_max_rpm, 0)} rpm · {fmt(lim.spindle_power_kw, 1)} kW
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <Cpu className="mt-0.5 size-3.5 shrink-0 text-muted" />
          <div>
            <div className="font-medium">Controller</div>
            <div className="text-muted">
              {m.controller.manufacturer} {m.controller.model}
              <br />
              version <span className={cn(m.controller.version.includes("unspecified") && "text-warn")}>{m.controller.version}</span>
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <FileCode2 className="mt-0.5 size-3.5 shrink-0 text-muted" />
          <div>
            <div className="font-medium">Postprocessor</div>
            {m.postprocessor ? (
              <div className="space-y-1 text-muted">
                <div>
                  {m.postprocessor.key} {m.postprocessor.version}
                </div>
                <VerificationBadge status={m.postprocessor.status} />
              </div>
            ) : (
              <div className="text-err">none assigned</div>
            )}
          </div>
        </div>
      </div>
      {!supported && <div className="mt-3 text-xs font-medium text-err">This machine type is not supported by the 3-axis milling workflow.</div>}
    </button>
  );
}
