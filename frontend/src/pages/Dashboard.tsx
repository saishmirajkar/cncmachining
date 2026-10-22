import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ClipboardCheck, Cpu, PlusSquare } from "lucide-react";
import { Link } from "react-router-dom";
import { JobStatusBadge, SeverityIcon } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { CamJob } from "@/lib/types";
import { fmtDate, humanize } from "@/lib/utils";

interface DashboardData {
  recent_jobs: CamJob[];
  active_jobs: CamJob[];
  failed_jobs: CamJob[];
  awaiting_review: { id: string; program_name: string; created_at: string; latest_revision_id: string | null }[];
  released: { revision_id: string; program_name: string; revision_number: number; released_at: string }[];
  recent_validation_issues: { program_name: string; revision_id: string; code: string; message: string; layer: string; created_at: string }[];
  machine_configuration_count: number;
  job_counts_by_status: Record<string, number>;
}

function JobRow({ j }: { j: CamJob }) {
  return (
    <Link to={`/history/${j.id}`} className="flex items-center justify-between gap-3 border-b border-border px-5 py-2.5 last:border-0 hover:bg-surface-2/60">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">{j.design_filename}</div>
        <div className="truncate text-xs text-muted">
          {j.project_name} · {j.machine} · {fmtDate(j.created_at)}
        </div>
      </div>
      <JobStatusBadge status={j.status} />
    </Link>
  );
}

function Panel({ title, count, children, empty }: { title: string; count?: number; children: React.ReactNode; empty: boolean }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>{title}</CardTitle>
        {count !== undefined && <span className="text-xs text-muted tabular">{count}</span>}
      </CardHeader>
      <div className="border-t border-border">
        {empty ? <div className="px-5 py-6 text-center text-sm text-muted">Nothing here.</div> : children}
      </div>
    </Card>
  );
}

export function Dashboard() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardData>("/api/dashboard"), refetchInterval: 10_000 });
  const d = q.data;
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Current CAM work, programs awaiting engineering review and recent validation findings in this organisation."
        actions={
          <Button asChild>
            <Link to="/jobs/new">
              <PlusSquare /> New CNC job
            </Link>
          </Button>
        }
      />
      {q.isLoading || !d ? (
        <div className="grid gap-4 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      ) : d.recent_jobs.length === 0 && d.awaiting_review.length === 0 ? (
        <EmptyState icon={Cpu} title="No CAM jobs yet">
          Start with <Link to="/jobs/new" className="font-medium text-accent">a new CNC job</Link>: upload a STEP model, select a
          configured machine, and the platform plans, computes, postprocesses and validates the program.{" "}
          {d.machine_configuration_count} machine configuration(s) available.
        </EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Panel title="Active CAM processing" count={d.active_jobs.length} empty={!d.active_jobs.length}>
              {d.active_jobs.map((j) => (
                <JobRow key={j.id} j={j} />
              ))}
            </Panel>
            <Panel title="Recent jobs" empty={!d.recent_jobs.length}>
              {d.recent_jobs.map((j) => (
                <JobRow key={j.id} j={j} />
              ))}
            </Panel>
            <Panel title="Failed jobs" count={d.failed_jobs.length} empty={!d.failed_jobs.length}>
              {d.failed_jobs.map((j) => (
                <JobRow key={j.id} j={j} />
              ))}
            </Panel>
          </div>
          <div className="space-y-4">
            <Panel title="Awaiting engineering review" count={d.awaiting_review.length} empty={!d.awaiting_review.length}>
              {d.awaiting_review.map((p) => (
                <Link key={p.id} to={`/programs/${p.id}`} className="flex items-center justify-between gap-2 border-b border-border px-5 py-2.5 text-sm last:border-0 hover:bg-surface-2/60">
                  <span className="truncate font-medium">{p.program_name}</span>
                  <ArrowRight className="size-4 text-muted" />
                </Link>
              ))}
            </Panel>
            <Panel title="Released for controlled export" count={d.released.length} empty={!d.released.length}>
              {d.released.map((r) => (
                <Link key={r.revision_id} to={`/revisions/${r.revision_id}`} className="block border-b border-border px-5 py-2.5 text-sm last:border-0 hover:bg-surface-2/60">
                  <div className="font-medium">
                    {r.program_name} <span className="text-muted">R{r.revision_number}</span>
                  </div>
                  <div className="text-xs text-muted">{fmtDate(r.released_at)}</div>
                </Link>
              ))}
            </Panel>
            <Panel title="Recent validation errors" empty={!d.recent_validation_issues.length}>
              {d.recent_validation_issues.map((i, k) => (
                <Link key={k} to={`/revisions/${i.revision_id}`} className="flex gap-2.5 border-b border-border px-5 py-2.5 last:border-0 hover:bg-surface-2/60">
                  <SeverityIcon severity="error" className="mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0 text-sm">
                    <div className="truncate font-medium">{i.program_name}</div>
                    <div className="line-clamp-2 text-xs text-muted">
                      {humanize(i.layer)} · {i.message}
                    </div>
                  </div>
                </Link>
              ))}
            </Panel>
            <Card>
              <CardContent className="flex items-center justify-between pt-4">
                <div className="flex items-center gap-2 text-sm">
                  <ClipboardCheck className="size-4 text-muted" /> Machine configurations available
                </div>
                <Link to="/machines" className="text-sm font-semibold text-accent tabular">
                  {d.machine_configuration_count}
                </Link>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
