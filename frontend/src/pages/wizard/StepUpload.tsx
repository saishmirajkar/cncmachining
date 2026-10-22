import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FileBox } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { FileDropzone } from "@/components/FileDropzone";
import { JobStatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/form";
import { Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Design, Project } from "@/lib/types";
import { fmtBytes, fmtDate } from "@/lib/utils";

export function StepUpload() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  const projectId = params.get("project") ?? projects.data?.[0]?.id ?? "";
  const [newName, setNewName] = useState("");
  const designs = useQuery({
    queryKey: ["designs", projectId],
    queryFn: () => api<Design[]>(`/api/projects/${projectId}/designs`),
    enabled: Boolean(projectId),
  });
  const create = useMutation({
    mutationFn: (name: string) => api<Project>("/api/projects", { method: "POST", json: { name } }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setParams({ project: p.id });
      setNewName("");
    },
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Project</CardTitle>
            <CardDescription>Designs, setups and programs are grouped in a project.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Existing project">
            <Select value={projectId} onChange={(e) => setParams({ project: e.target.value })} data-testid="project-select">
              {!projects.data?.length && <option value="">No projects yet</option>}
              {projects.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.part_number ? ` (${p.part_number})` : ""}
                </option>
              ))}
            </Select>
          </Field>
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) create.mutate(newName.trim());
            }}
          >
            <Field label="…or create a new project" className="flex-1">
              <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Hydraulic manifold block" data-testid="new-project-name" />
            </Field>
            <Button type="submit" variant="outline" loading={create.isPending} disabled={!newName.trim()}>
              Create
            </Button>
          </form>
          {create.error && <p className="text-sm text-err">{(create.error as Error).message}</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Engineering design</CardTitle>
            <CardDescription>
              The file is stored unchanged, its content is verified, and its geometry is analysed in an isolated worker.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {projectId ? (
            <FileDropzone
              projectId={projectId}
              onUploaded={(d) => {
                qc.invalidateQueries({ queryKey: ["designs", projectId] });
                nav(`/jobs/new/design/${d.id}`);
              }}
            />
          ) : (
            <p className="text-sm text-muted">Create a project first.</p>
          )}
        </CardContent>
      </Card>
      {designs.data && designs.data.length > 0 && (
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Designs in this project</CardTitle>
          </CardHeader>
          <Table>
            <thead>
              <tr>
                <Th>File</Th>
                <Th>Format</Th>
                <Th>Rev</Th>
                <Th>Size</Th>
                <Th>Units</Th>
                <Th>Status</Th>
                <Th>Uploaded</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {designs.data.map((d) => (
                <tr key={d.id}>
                  <Td className="font-medium">
                    <span className="inline-flex items-center gap-2">
                      <FileBox className="size-4 text-muted" />
                      {d.original_filename}
                    </span>
                  </Td>
                  <Td className="uppercase">{d.file_format}</Td>
                  <Td>{d.revision}</Td>
                  <Td>{fmtBytes(d.size_bytes)}</Td>
                  <Td>{d.confirmed_units ?? d.detected_units ?? "unknown"}</Td>
                  <Td>
                    <JobStatusBadge status={d.status} />
                  </Td>
                  <Td className="text-muted">{fmtDate(d.created_at)}</Td>
                  <Td className="text-right">
                    <Button size="sm" variant="outline" onClick={() => nav(`/jobs/new/design/${d.id}`)}>
                      Use <ArrowRight />
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
