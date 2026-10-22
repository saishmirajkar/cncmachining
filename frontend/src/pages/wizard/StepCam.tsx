import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RotateCcw, Square } from "lucide-react";
import { useRef, useState } from "react";
import { JobStatusBadge, SeverityIcon } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, NumberInput } from "@/components/ui/form";
import { Alert, KV, Progress, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { CamJob, Setup } from "@/lib/types";
import { fmtDate } from "@/lib/utils";
import { StepNav } from "./common";

export function StepCam({ setup, job }: { setup: Setup; job: CamJob | null }) {
  const qc = useQueryClient();
  const key = useRef<string>(crypto.randomUUID());
  const [programNumber, setProgramNumber] = useState<number | null>(null);
  const cfg = setup.machine_configuration?.postprocessor?.config;
  const needsNumber = Boolean(cfg?.program?.number_template);
  const refresh = () => qc.invalidateQueries({ queryKey: ["jobs", "setup", setup.id] });
  const run = useMutation({
    mutationFn: () => api<CamJob>(`/api/setups/${setup.id}/cam-jobs`, { method: "POST", json: { idempotency_key: key.current, program_number: programNumber } }),
    onSuccess: () => {
      key.current = crypto.randomUUID();
      refresh();
    },
  });
  const cancel = useMutation({ mutationFn: (id: string) => api(`/api/cam-jobs/${id}/cancel`, { method: "POST" }), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (id: string) => api(`/api/cam-jobs/${id}/retry`, { method: "POST" }), onSuccess: refresh });
  const busy = job && ["ready_for_cam", "cam_processing", "postprocessing"].includes(job.status);
  const detail = useQuery({
    queryKey: ["job", job?.id, job?.status],
    queryFn: () => api<CamJob>(`/api/cam-jobs/${job!.id}`),
    enabled: Boolean(job) && !busy,
  });
  const shown = detail.data ?? null;

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Run the CAM engine</CardTitle>
              <CardDescription>
                Freezes an immutable snapshot of the geometry revision, stock, tools, machine configuration and postprocessor version, then
                computes toolpaths with FreeCAD CAM in an isolated worker process.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {!setup.plan_approved && <Alert tone="warn">Approve the machining plan first.</Alert>}
            {needsNumber && (
              <Field label="Program number" required hint={`${cfg?.name}: ${cfg?.program.number_min}–${cfg?.program.number_max}. Use the number reserved for this part on the control.`}>
                <NumberInput value={programNumber} onChange={setProgramNumber} step={1} />
              </Field>
            )}
            <Button onClick={() => run.mutate()} loading={run.isPending} disabled={!setup.plan_approved || Boolean(busy) || (needsNumber && !programNumber)} data-testid="run-cam">
              <Play /> {job ? "Run again with current plan" : "Generate toolpaths"}
            </Button>
            {run.error && <Alert tone="err">{(run.error as Error).message}</Alert>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">Latest job {job && <JobStatusBadge status={job.status} />}</CardTitle>
              {job && (
                <CardDescription>
                  {fmtDate(job.created_at)} · snapshot {job.snapshot_sha256.slice(0, 12)}… · {job.engine ? `${job.engine} ${job.engine_version}` : "engine pending"}
                </CardDescription>
              )}
            </div>
            <div className="flex gap-2">
              {busy && (
                <Button variant="outline" size="sm" onClick={() => cancel.mutate(job!.id)}>
                  <Square /> Cancel
                </Button>
              )}
              {job?.status === "cam_failed" && (
                <Button variant="outline" size="sm" onClick={() => retry.mutate(job.id)} loading={retry.isPending}>
                  <RotateCcw /> Retry
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {!job ? (
              <p className="text-sm text-muted">No CAM job has been run for this setup.</p>
            ) : (
              <>
                <div>
                  <div className="mb-1.5 flex justify-between text-xs text-muted">
                    <span>{job.progress_message}</span>
                    <span className="tabular">{Math.round(job.progress * 100)}%</span>
                  </div>
                  <Progress value={job.progress} />
                </div>
                {job.error && (
                  <Alert tone="err" title="CAM did not complete">
                    {job.error}
                  </Alert>
                )}
                <KV cols={3} items={[["Machine", job.machine], ["Postprocessor", `${job.postprocessor.key} ${job.postprocessor.version}`], ["Attempts", job.attempts]]} />
              </>
            )}
          </CardContent>
          {shown?.operation_results && shown.operation_results.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <Th>Operation</Th>
                  <Th>Result</Th>
                  <Th>Engine commands</Th>
                  <Th>IR moves</Th>
                </tr>
              </thead>
              <tbody>
                {shown.operation_results.map((r) => (
                  <tr key={r.operation_id}>
                    <Td>
                      <div className="font-medium">{r.name}</div>
                      {r.messages.map((m, i) => (
                        <div key={i} className="text-xs text-err">
                          {m}
                        </div>
                      ))}
                    </Td>
                    <Td>
                      <SeverityIcon severity={r.status === "ok" ? "ok" : "error"} />
                    </Td>
                    <Td>{r.command_count}</Td>
                    <Td>{r.move_count ?? "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          {shown?.log && (
            <CardContent className="pt-4">
              <div className="max-h-48 overflow-y-auto rounded-md bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">
                {shown.log.map((l, i) => (
                  <div key={i}>
                    <span className="text-muted">{new Date(l.at).toLocaleTimeString()}</span> {l.message}
                  </div>
                ))}
              </div>
            </CardContent>
          )}
        </Card>
      </div>
      <StepNav setup={setup} current="cam" nextDisabled={!job?.program_ids.length} />
    </>
  );
}
