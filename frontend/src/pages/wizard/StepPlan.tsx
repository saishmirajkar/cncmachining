import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Info, Pencil, RefreshCw } from "lucide-react";
import { useState } from "react";
import { SeverityIcon } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, NumberInput, Select } from "@/components/ui/form";
import { Alert, Dialog, DialogContent, EmptyState, Table, Td, Th, Tip } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import type { Operation, Setup, ToolLibrary } from "@/lib/types";
import { fmt, humanize } from "@/lib/utils";
import { RequirementList, StepNav } from "./common";


function Provenance({ op, k, unit }: { op: Operation; k: string; unit?: string }) {
  const p = op.parameter_sources?.[k];
  const value = (op as any)[k];
  return (
    <Tip content={p ? `${humanize(p.source)}: ${p.detail}` : "No provenance recorded"}>
      <span className="inline-flex items-center gap-1">
        {fmt(value, 1)}
        {unit && <span className="text-muted">{unit}</span>}
        {p && <span className={`size-1.5 rounded-full ${p.source === "material_table" ? "bg-warn" : p.source === "user" ? "bg-muted" : p.source === "tool_manufacturer" ? "bg-ok" : "bg-info"}`} />}
      </span>
    </Tip>
  );
}

function OperationDialog({ op, setup, tools, onClose }: { op: Operation; setup: Setup; tools: ToolLibrary | undefined; onClose: () => void }) {
  const qc = useQueryClient();
  const [o, setO] = useState({ ...op });
  const save = useMutation({
    mutationFn: () =>
      api<Setup>(`/api/operations/${op.id}`, {
        method: "PUT",
        json: {
          name: o.name, enabled: o.enabled, tool_id: o.tool_id, spindle_rpm: o.spindle_rpm, feed_mm_min: o.feed_mm_min,
          plunge_feed_mm_min: o.plunge_feed_mm_min, stepdown_mm: o.stepdown_mm, stepover_percent: o.stepover_percent,
          start_depth_mm: o.start_depth_mm, final_depth_mm: o.final_depth_mm, clearance_height_mm: o.clearance_height_mm,
          safe_height_mm: o.safe_height_mm, cut_direction: o.cut_direction, coolant: o.coolant, finish_allowance_mm: o.finish_allowance_mm,
          parameters: o.parameters,
        },
      }),
    onSuccess: (s) => {
      qc.setQueryData(["setup", setup.id], s);
      onClose();
    },
  });
  const num = (k: keyof Operation) => (v: number | null) => setO({ ...o, [k]: v });
  return (
    <DialogContent title={`Operation ${op.sequence}: ${humanize(op.op_type)}`} description={op.strategy} className="w-[min(760px,calc(100vw-2rem))]">
      <div className="grid grid-cols-3 gap-3">
        <Field label="Name" className="col-span-2">
          <Input value={o.name} onChange={(e) => setO({ ...o, name: e.target.value })} />
        </Field>
        <Field label="Enabled">
          <Select value={o.enabled ? "1" : "0"} onChange={(e) => setO({ ...o, enabled: e.target.value === "1" })}>
            <option value="1">Yes</option>
            <option value="0">No (skip)</option>
          </Select>
        </Field>
        <Field label="Tool" className="col-span-2">
          <Select value={o.tool_id ?? ""} onChange={(e) => setO({ ...o, tool_id: e.target.value })}>
            {tools?.tools.map((t) => (
              <option key={t.id} value={t.id}>
                T{t.tool_number} — {t.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Cut direction">
          <Select value={o.cut_direction} onChange={(e) => setO({ ...o, cut_direction: e.target.value })}>
            <option value="climb">Climb</option>
            <option value="conventional">Conventional</option>
          </Select>
        </Field>
        <Field label="Spindle" unit="rpm">
          <NumberInput value={o.spindle_rpm} onChange={num("spindle_rpm")} />
        </Field>
        <Field label="Feed" unit="mm/min">
          <NumberInput value={o.feed_mm_min} onChange={num("feed_mm_min")} />
        </Field>
        <Field label="Plunge feed" unit="mm/min">
          <NumberInput value={o.plunge_feed_mm_min} onChange={num("plunge_feed_mm_min")} />
        </Field>
        <Field label="Start depth (WCS Z)" unit="mm">
          <NumberInput value={o.start_depth_mm} onChange={num("start_depth_mm")} />
        </Field>
        <Field label="Final depth (WCS Z)" unit="mm">
          <NumberInput value={o.final_depth_mm} onChange={num("final_depth_mm")} />
        </Field>
        <Field label="Stepdown" unit="mm">
          <NumberInput value={o.stepdown_mm} onChange={num("stepdown_mm")} />
        </Field>
        <Field label="Stepover" unit="%">
          <NumberInput value={o.stepover_percent} onChange={num("stepover_percent")} />
        </Field>
        <Field label="Clearance Z" unit="mm">
          <NumberInput value={o.clearance_height_mm} onChange={num("clearance_height_mm")} />
        </Field>
        <Field label="Safe Z" unit="mm">
          <NumberInput value={o.safe_height_mm} onChange={num("safe_height_mm")} />
        </Field>
        {op.op_type === "drill" && (
          <>
            <Field label="Cycle">
              <Select value={o.parameters.cycle} onChange={(e) => setO({ ...o, parameters: { ...o.parameters, cycle: e.target.value } })}>
                <option value="drill">Drill (G81)</option>
                <option value="peck">Peck (G83)</option>
                <option value="chip_break">Chip break (G73)</option>
                <option value="drill_dwell">Drill with dwell (G82)</option>
              </Select>
            </Field>
            <Field label="Peck depth" unit="mm">
              <NumberInput value={o.parameters.peck_mm ?? null} onChange={(v) => setO({ ...o, parameters: { ...o.parameters, peck_mm: v } })} />
            </Field>
          </>
        )}
        <Field label="Coolant">
          <Select value={o.coolant} onChange={(e) => setO({ ...o, coolant: e.target.value })}>
            {["off", "flood", "mist", "air"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
        </Field>
      </div>
      {save.error && <Alert tone="err" className="mt-3">{(save.error as Error).message}</Alert>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={() => save.mutate()} loading={save.isPending}>
          Save operation
        </Button>
      </div>
    </DialogContent>
  );
}

export function StepPlan({ setup }: { setup: Setup }) {
  const qc = useQueryClient();
  const libs = useQuery({ queryKey: ["tool-libraries"], queryFn: () => api<ToolLibrary[]>("/api/tool-libraries") });
  const lib = libs.data?.find((l) => l.id === setup.tool_library_id);
  const toolNo = (id: string | null) => lib?.tools.find((t) => t.id === id);
  const [edit, setEdit] = useState<Operation | null>(null);
  const [ack, setAck] = useState(false);
  const plan = useMutation({
    mutationFn: () => api<Setup>(`/api/setups/${setup.id}/plan`, { method: "POST" }),
    onSuccess: (s) => qc.setQueryData(["setup", setup.id], s),
  });
  const approve = useMutation({
    mutationFn: () => api<Setup>(`/api/setups/${setup.id}/approve-plan`, { method: "POST", json: { acknowledge_unresolved: ack } }),
    onSuccess: (s) => qc.setQueryData(["setup", setup.id], s),
  });
  const ops = setup.operations;
  const unresolved = setup.plan_report?.unresolved ?? [];
  const issues = setup.plan_report?.issues ?? [];
  const blockingReqs = setup.requirements.filter((r) => r.severity === "error");
  const approveErr = approve.error as ApiError | null;

  return (
    <>
      {!setup.ready_for_planning && (
        <Alert tone="warn" title="Setup incomplete" className="mb-4">
          <RequirementList items={blockingReqs} />
        </Alert>
      )}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Proposed operations</CardTitle>
            <CardDescription>
              Generated by deterministic rules from the recognised features, your tools and the material data. Coloured dots show the
              source of each value: <span className="text-ok">manufacturer</span> · <span className="text-warn">generic table estimate</span> ·{" "}
              <span className="text-info">calculated</span> · <span className="text-accent">machine limit</span> · <span className="text-muted">user</span>.
            </CardDescription>
          </div>
          <Button variant={ops.length ? "outline" : "default"} onClick={() => plan.mutate()} loading={plan.isPending} disabled={!setup.ready_for_planning} data-testid="generate-plan">
            <RefreshCw /> {ops.length ? "Re-plan" : "Generate plan"}
          </Button>
        </CardHeader>
        {plan.error && (
          <CardContent>
            <Alert tone="err">{(plan.error as Error).message}</Alert>
          </CardContent>
        )}
        {ops.length === 0 ? (
          <CardContent>
            <EmptyState icon={Info} title="No plan yet">
              Generate a plan once the setup is complete. Re-planning replaces all operations, including your edits.
            </EmptyState>
          </CardContent>
        ) : (
          <Table data-testid="ops-table">
            <thead>
              <tr>
                <Th>#</Th>
                <Th>Operation</Th>
                <Th>Tool</Th>
                <Th>Spindle</Th>
                <Th>Feed / plunge</Th>
                <Th>Z start → final</Th>
                <Th>Stepdown / over</Th>
                <Th>Entry</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {ops.map((o) => {
                const t = toolNo(o.tool_id);
                return (
                  <tr key={o.id} className={o.enabled ? undefined : "opacity-45"}>
                    <Td className="text-muted">{o.sequence}</Td>
                    <Td>
                      <div className="font-medium">{o.name}</div>
                      <div className="text-xs text-muted">
                        {humanize(o.op_type)} · {o.strategy}
                        {o.user_modified && <Badge className="ml-1.5">edited</Badge>}
                      </div>
                      {o.issues?.map((i, k) => (
                        <div key={k} className="mt-1 flex gap-1.5 text-xs text-warn">
                          <SeverityIcon severity="warning" className="size-3.5" /> {i}
                        </div>
                      ))}
                    </Td>
                    <Td className="whitespace-nowrap">{t ? `T${t.tool_number} · Ø${fmt(t.diameter_mm)}` : "—"}</Td>
                    <Td className="whitespace-nowrap">
                      <Provenance op={o} k="spindle_rpm" />
                    </Td>
                    <Td className="whitespace-nowrap">
                      <Provenance op={o} k="feed_mm_min" /> / {fmt(o.plunge_feed_mm_min, 0)}
                    </Td>
                    <Td className="whitespace-nowrap">
                      {fmt(o.start_depth_mm)} → {fmt(o.final_depth_mm)}
                    </Td>
                    <Td className="whitespace-nowrap">
                      {fmt(o.stepdown_mm)} / {o.stepover_percent ? `${fmt(o.stepover_percent, 0)}%` : "—"}
                    </Td>
                    <Td className="text-xs text-muted">{o.entry_strategy || "—"}</Td>
                    <Td>
                      <Button size="icon" variant="ghost" onClick={() => setEdit(o)} aria-label="Edit operation">
                        <Pencil />
                      </Button>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
      {(unresolved.length > 0 || issues.length > 0) && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Unresolved decisions ({unresolved.length})</CardTitle>
                <CardDescription>The planner did not substitute operations for these. They must be resolved or knowingly accepted.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {unresolved.map((u, i) => (
                <div key={i} className="flex gap-2 text-sm" data-testid="unresolved">
                  <SeverityIcon severity="warning" className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <span className="font-mono text-xs text-muted">{u.code}</span> — {u.message}
                  </div>
                </div>
              ))}
              {!unresolved.length && <p className="text-sm text-muted">None.</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Planner notes</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {issues.map((u, i) => (
                <div key={i} className="flex gap-2 text-sm">
                  <SeverityIcon severity={u.severity} className="mt-0.5 size-4 shrink-0" />
                  <span>{u.message}</span>
                </div>
              ))}
              {!issues.length && <p className="text-sm text-muted">None.</p>}
            </CardContent>
          </Card>
        </div>
      )}
      {ops.length > 0 && (
        <Card className="mt-4">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-5">
            {setup.plan_approved ? (
              <div className="flex items-center gap-2 text-sm font-medium text-ok">
                <CheckCircle2 className="size-4" /> Plan approved for CAM computation
              </div>
            ) : (
              <label className="flex max-w-2xl items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} disabled={!unresolved.length} data-testid="ack-unresolved" />
                <span>
                  {unresolved.length
                    ? `I have reviewed the ${unresolved.length} unresolved decision(s). The affected features will not be machined by this program.`
                    : "No unresolved decisions."}
                </span>
              </label>
            )}
            {!setup.plan_approved && (
              <Button onClick={() => approve.mutate()} loading={approve.isPending} disabled={unresolved.length > 0 && !ack} data-testid="approve-plan">
                Approve plan
              </Button>
            )}
          </CardContent>
          {approveErr && (
            <CardContent>
              <Alert tone="err" title={approveErr.message}>
                {(approveErr.detail as any)?.blockers?.join("; ")}
              </Alert>
            </CardContent>
          )}
        </Card>
      )}
      <Dialog open={Boolean(edit)} onOpenChange={(o) => !o && setEdit(null)}>
        {edit && <OperationDialog op={edit} setup={setup} tools={lib} onClose={() => setEdit(null)} />}
      </Dialog>
      <StepNav setup={setup} current="plan" nextDisabled={!setup.plan_approved} />
    </>
  );
}
