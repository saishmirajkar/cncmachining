import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { JobStatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/form";
import { Dialog, DialogContent, EmptyState, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { CamJob, Design, Project } from "@/lib/types";
import { fmtBytes, fmtDate } from "@/lib/utils";

export function Projects() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", part_number: "", customer_reference: "", description: "" });
  const create = useMutation({
    mutationFn: () => api("/api/projects", { method: "POST", json: form }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setOpen(false);
    },
  });
  return (
    <>
      <PageHeader
        title="Projects"
        description="Each project groups engineering design revisions, machining setups, CAM jobs and programs for one part."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus /> New project
          </Button>
        }
      />
      {list.isLoading ? (
        <Skeleton className="h-64" />
      ) : !list.data?.length ? (
        <EmptyState icon={FolderKanban} title="No projects yet" />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Project</Th>
                <Th>Part number</Th>
                <Th>Customer</Th>
                <Th>Designs</Th>
                <Th>CAM jobs</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((p) => (
                <tr key={p.id} className="hover:bg-surface-2/60">
                  <Td>
                    <Link to={`/projects/${p.id}`} className="font-medium text-accent hover:underline">
                      {p.name}
                    </Link>
                    {p.description && <div className="text-xs text-muted">{p.description}</div>}
                  </Td>
                  <Td>{p.part_number || "—"}</Td>
                  <Td>{p.customer_reference || "—"}</Td>
                  <Td>{p.design_count}</Td>
                  <Td>{p.job_count}</Td>
                  <Td className="text-muted">{fmtDate(p.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="New project">
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="Name" required>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Part number">
                <Input value={form.part_number} onChange={(e) => setForm({ ...form, part_number: e.target.value })} />
              </Field>
              <Field label="Customer reference">
                <Input value={form.customer_reference} onChange={(e) => setForm({ ...form, customer_reference: e.target.value })} />
              </Field>
            </div>
            <Field label="Description">
              <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            {create.error && <p className="text-sm text-err">{(create.error as Error).message}</p>}
            <div className="flex justify-end">
              <Button type="submit" loading={create.isPending}>
                Create project
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ProjectDetail() {
  const { projectId } = useParams();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api<Project>(`/api/projects/${projectId}`) });
  const designs = useQuery({ queryKey: ["designs", projectId], queryFn: () => api<Design[]>(`/api/projects/${projectId}/designs`) });
  const setups = useQuery({ queryKey: ["setups", projectId], queryFn: () => api<{ id: string; name: string; design_id: string; status: string; created_at: string }[]>(`/api/projects/${projectId}/setups`) });
  const jobs = useQuery({ queryKey: ["jobs", "project", projectId], queryFn: () => api<CamJob[]>(`/api/cam-jobs?project_id=${projectId}`) });
  if (!project.data) return <Skeleton className="h-96" />;
  const p = project.data;
  return (
    <>
      <PageHeader
        eyebrow={<Link to="/projects">Projects</Link>}
        title={p.name}
        description={[p.part_number && `Part ${p.part_number}`, p.customer_reference && `Customer ${p.customer_reference}`, p.description].filter(Boolean).join(" · ")}
        actions={
          <Button asChild>
            <Link to={`/jobs/new?project=${p.id}`}>
              <Plus /> Upload design
            </Link>
          </Button>
        }
      />
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Design revisions</CardTitle>
          </CardHeader>
          <Table>
            <thead>
              <tr>
                <Th>File</Th>
                <Th>Rev</Th>
                <Th>Format</Th>
                <Th>Size</Th>
                <Th>SHA-256</Th>
                <Th>Status</Th>
                <Th>Uploaded</Th>
              </tr>
            </thead>
            <tbody>
              {designs.data?.map((d) => (
                <tr key={d.id}>
                  <Td>
                    <Link to={`/jobs/new/design/${d.id}`} className="font-medium text-accent hover:underline">
                      {d.original_filename}
                    </Link>
                  </Td>
                  <Td>{d.revision}</Td>
                  <Td className="uppercase">{d.file_format}</Td>
                  <Td>{fmtBytes(d.size_bytes)}</Td>
                  <Td className="font-mono text-xs">{d.sha256.slice(0, 12)}…</Td>
                  <Td>
                    <JobStatusBadge status={d.status} />
                  </Td>
                  <Td className="text-muted">{fmtDate(d.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Machining setups</CardTitle>
          </CardHeader>
          <Table>
            <thead>
              <tr>
                <Th>Setup</Th>
                <Th>Status</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {setups.data?.map((s) => (
                <tr key={s.id}>
                  <Td>
                    <Link to={`/jobs/${s.id}/machine`} className="font-medium text-accent hover:underline">
                      {s.name}
                    </Link>
                  </Td>
                  <Td>
                    <JobStatusBadge status={s.status} />
                  </Td>
                  <Td className="text-muted">{fmtDate(s.created_at)}</Td>
                </tr>
              ))}
              {!setups.data?.length && (
                <tr>
                  <Td colSpan={3} className="text-muted">
                    No setups.
                  </Td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>CAM jobs</CardTitle>
          </CardHeader>
          <JobsTable jobs={jobs.data ?? []} />
        </Card>
      </div>
    </>
  );
}

export function JobsTable({ jobs }: { jobs: CamJob[] }) {
  return (
    <Table>
      <thead>
        <tr>
          <Th>Job</Th>
          <Th>Design</Th>
          <Th>Machine</Th>
          <Th>Postprocessor</Th>
          <Th>Status</Th>
          <Th>Created</Th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="hover:bg-surface-2/60">
            <Td>
              <Link to={`/history/${j.id}`} className="font-mono text-xs text-accent hover:underline">
                {j.id.slice(0, 8)}
              </Link>
            </Td>
            <Td>{j.design_filename}</Td>
            <Td>{j.machine}</Td>
            <Td className="text-xs">
              {j.postprocessor.key} {j.postprocessor.version}
            </Td>
            <Td>
              <JobStatusBadge status={j.status} />
            </Td>
            <Td className="text-muted">{fmtDate(j.created_at)}</Td>
          </tr>
        ))}
        {!jobs.length && (
          <tr>
            <Td colSpan={6} className="py-6 text-center text-muted">
              No jobs.
            </Td>
          </tr>
        )}
      </tbody>
    </Table>
  );
}
