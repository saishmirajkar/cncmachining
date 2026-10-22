import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { MachineCard, MILLING } from "@/components/MachineCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/form";
import { Alert, KV, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { MachineConfig, Setup } from "@/lib/types";
import { fmt, humanize } from "@/lib/utils";
import { RequirementList, StepNav, usePatchSetup } from "./common";

const BLOCKS = ["program_start", "pre_tool_change", "tool_change", "end_parking", "coolant"];

export function StepMachine({ setup }: { setup: Setup }) {
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["machines", q], queryFn: () => api<MachineConfig[]>(`/api/catalogue/machine-configurations?q=${encodeURIComponent(q)}`) });
  const patch = usePatchSetup(setup.id);
  const sel = setup.machine_configuration;

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="space-y-3">
          <div className="relative">
            <Search className="absolute top-2.5 left-2.5 size-4 text-muted" />
            <Input className="pl-8" placeholder="Search manufacturer, model, controller, machine type…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {list.isLoading && <Skeleton className="h-40" />}
          {list.data?.map((m) => (
            <MachineCard key={m.id} m={m} selected={m.id === setup.machine_configuration_id} onSelect={() => patch.mutate({ machine_configuration_id: m.id })} />
          ))}
          {list.data?.length === 0 && <p className="text-sm text-muted">No machine configuration matches. Platform administrators add machines in the Machine Library.</p>}
          {patch.error && <Alert tone="err">{(patch.error as Error).message}</Alert>}
        </div>
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Selected configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!sel ? (
              <p className="text-sm text-muted">Choose the physical machine and its exact controller configuration. The postprocessor is fixed by the configuration.</p>
            ) : (
              <>
                <div className="text-sm font-semibold">{sel.name}</div>
                {!MILLING.has(sel.machine_model.category) && (
                  <Alert tone="err" title="Unsupported machine type">
                    {humanize(sel.machine_model.category)} machines are not supported in this release (3-axis milling only).
                  </Alert>
                )}
                {sel.is_example && (
                  <Alert tone="warn" title="Example configuration">
                    {sel.source}
                  </Alert>
                )}
                <KV
                  cols={2}
                  items={[
                    ["Travel X / Y / Z", `${fmt(sel.effective_limits.travel_x_mm, 0)} / ${fmt(sel.effective_limits.travel_y_mm, 0)} / ${fmt(sel.effective_limits.travel_z_mm, 0)} mm`],
                    ["Spindle", `${fmt(sel.effective_limits.spindle_min_rpm, 0)}–${fmt(sel.effective_limits.spindle_max_rpm, 0)} rpm`],
                    ["Max feed / rapid", `${fmt(sel.effective_limits.max_feed_mm_min, 0)} / ${fmt(sel.effective_limits.rapid_rate_mm_min, 0)} mm/min`],
                    ["Tool changer", sel.effective_limits.tool_changer?.capacity ? `${sel.effective_limits.tool_changer.capacity} pockets, Ø≤${sel.effective_limits.tool_changer.max_tool_diameter_mm ?? "?"}` : "—"],
                    ["Coolant", sel.machine_model.coolant_options.join(", ") || "none"],
                    ["Operations enabled", sel.supported_operations.join(", ")],
                    ["Controller", `${sel.controller.manufacturer} ${sel.controller.model} ${sel.controller.version}`],
                    ["Output", sel.postprocessor ? `${sel.postprocessor.dialect} (${sel.postprocessor.file_extension})` : "—"],
                  ]}
                />
                <div>
                  <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Machine-specific blocks (owner supplied)</div>
                  <div className="flex flex-wrap gap-1.5">
                    {BLOCKS.map((b) => (
                      <span key={b} className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${sel.machine_blocks?.[b] ? "border-ok/30 bg-ok-soft text-ok" : "border-border bg-surface-2 text-muted"}`}>
                        {b}: {sel.machine_blocks?.[b] ? "configured" : "not configured"}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-xs text-muted">
                    Missing blocks are omitted from programs with a warning; the platform never invents parking, tool-change or coolant sequences.
                  </p>
                </div>
                {sel.known_limitations.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
                    {sel.known_limitations.concat(sel.postprocessor?.known_limitations ?? []).map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                )}
                <RequirementList items={setup.requirements} fields={["machine_configuration", "machine"]} />
              </>
            )}
          </CardContent>
        </Card>
      </div>
      <StepNav setup={setup} current="machine" nextDisabled={!sel} />
    </>
  );
}
