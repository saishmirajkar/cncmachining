import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { JobStatusBadge, SeverityIcon, ValidationBadge } from "@/components/status";
import { ProgramWorkspace } from "@/components/program/ProgramWorkspace";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/form";
import { Alert, KV, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { CamJob, Revision } from "@/lib/types";
import { fmtDate } from "@/lib/utils";
import { JobsTable } from "./Projects";
import { useState } from "react";

export function History() {
  const [status, setStatus] = useState("");
  const jobs = useQuery({ queryKey: ["jobs", "all", status], queryFn: () => api<CamJob[]>(`/api/cam-jobs${status ? `?status=${status}` : ""}`) });
  return (
    <>
      <PageHeader
        title="Job history"
        description="Every CAM job with its immutable input snapshot: design revision, stock, tools, machine configuration and postprocessor version."
        actions={
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-64">
            <option value="">All statuses</option>
            {["cam_failed", "validation_failed", "engineering_review_required", "ready_for_controlled_export", "cancelled"].map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </Select>
        }
      />
      <Card>{jobs.isLoading ? <Skeleton className="h-48" /> : <JobsTable jobs={jobs.data ?? []} />}</Card>
    </>
  );
}

export function JobDetail() {
  const { jobId } = useParams();
  const job = useQuery({
    queryKey: ["job-full", jobId],
    queryFn: () => api<CamJob>(`/api/cam-jobs/${jobId}`),
    refetchInterval: (q) => (["ready_for_cam", "cam_processing", "postprocessing"].includes(q.state.data?.status ?? "") ? 2000 : false),
  });
  if (!job.data) return <Skeleton className="h-96" />;
  const j = job.data;
  const s = j.input_snapshot!;
  return (
    <>
      <PageHeader eyebrow={<Link to="/history">Job history</Link>} title={`CAM job ${j.id.slice(0, 8)}`} description={`${j.design_filename} · ${j.machine}`} actions={<JobStatusBadge status={j.status} />} />
      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Traceability</CardTitle>
          </CardHeader>
          <CardContent>
            <KV
              cols={2}
              items={[
                ["Created", fmtDate(j.created_at)],
                ["Finished", fmtDate(j.finished_at)],
                ["CAM engine", j.engine ? `${j.engine} ${j.engine_version}` : "—"],
                ["Snapshot SHA-256", <span className="font-mono text-xs">{j.snapshot_sha256.slice(0, 24)}…</span>],
                ["Design", `${s.design.filename} R${s.design.revision}`],
                ["Design SHA-256", <span className="font-mono text-xs">{s.design.sha256.slice(0, 24)}…</span>],
                ["Postprocessor", `${s.postprocessor.key} ${s.postprocessor.version} (${s.postprocessor.status})`],
                ["Setup", `${s.setup.name} · ${s.setup.work_offset} · ${s.setup.datum}`],
                ["Operations", s.operations.length],
                ["Tools", s.tools.map((t: any) => `T${t.tool_number}`).join(" ")],
                ["Attempts", j.attempts],
                ["Artifacts", j.artifacts?.map((a) => `${a.format} (${a.stats.moves} moves)`).join(", ") || "—"],
              ]}
            />
            {j.error && (
              <Alert tone="err" className="mt-4" title="Error">
                {j.error}
              </Alert>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Operation results</CardTitle>
          </CardHeader>
          <Table>
            <thead>
              <tr>
                <Th>Operation</Th>
                <Th>Result</Th>
                <Th>Moves</Th>
              </tr>
            </thead>
            <tbody>
              {j.operation_results?.map((r) => (
                <tr key={r.operation_id}>
                  <Td>
                    {r.name}
                    {r.messages.map((m, i) => (
                      <div key={i} className="text-xs text-err">
                        {m}
                      </div>
                    ))}
                  </Td>
                  <Td>
                    <SeverityIcon severity={r.status === "ok" ? "ok" : "error"} />
                  </Td>
                  <Td>{r.move_count ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <CardContent className="pt-4">
            <div className="max-h-40 overflow-y-auto rounded-md bg-surface-2 p-3 font-mono text-[11px]">
              {j.log?.map((l, i) => (
                <div key={i}>
                  <span className="text-muted">{new Date(l.at).toLocaleTimeString()}</span> {l.message}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
      {j.program_ids[0] && <ProgramWorkspace programId={j.program_ids[0]} />}
    </>
  );
}

export function Reports() {
  const reps = useQuery({
    queryKey: ["reports"],
    queryFn: () => api<{ id: string; status: string; summary: Record<string, any>; created_at: string; revision_id: string; revision_number: number; program_id: string; program_name: string; top_issues: { code: string; message: string }[] }[]>("/api/validation-reports"),
  });
  return (
    <>
      <PageHeader title="Validation reports" description="Each report is tied to one exact program revision. Re-validation creates a new report; earlier reports remain for audit." />
      <Card>
        <Table>
          <thead>
            <tr>
              <Th>Program</Th>
              <Th>Revision</Th>
              <Th>Result</Th>
              <Th>Errors / warnings</Th>
              <Th>Top errors</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {reps.data?.map((r) => (
              <tr key={r.id} className="hover:bg-surface-2/60">
                <Td>
                  <Link to={`/revisions/${r.revision_id}`} className="font-medium text-accent hover:underline">
                    {r.program_name}
                  </Link>
                </Td>
                <Td>R{r.revision_number}</Td>
                <Td>
                  <ValidationBadge status={r.status} />
                </Td>
                <Td>
                  {r.summary.errors} / {r.summary.warnings}
                </Td>
                <Td className="max-w-md text-xs">
                  {r.top_issues.map((i) => (
                    <div key={i.code} className="truncate">
                      <span className="font-mono">{i.code}</span> {i.message}
                    </div>
                  ))}
                </Td>
                <Td className="text-muted">{fmtDate(r.created_at)}</Td>
              </tr>
            ))}
            {reps.data?.length === 0 && (
              <tr>
                <Td colSpan={6} className="py-6 text-center text-muted">
                  No reports yet.
                </Td>
              </tr>
            )}
          </tbody>
        </Table>
      </Card>
    </>
  );
}

export function ProgramPage() {
  const { programId } = useParams();
  return (
    <>
      <PageHeader eyebrow="Program" title="Program workspace" />
      <ProgramWorkspace programId={programId!} />
    </>
  );
}

export function RevisionPage() {
  const { revisionId } = useParams();
  const rev = useQuery({ queryKey: ["revision", revisionId], queryFn: () => api<Revision>(`/api/program-revisions/${revisionId}`) });
  if (!rev.data) return <Skeleton className="h-96" />;
  return (
    <>
      <PageHeader eyebrow="Program revision" title={`Revision R${rev.data.revision_number}`} />
      <ProgramWorkspace programId={rev.data.program_id} initialRevisionId={rev.data.id} defaultTab="validation" />
    </>
  );
}
