import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, FileArchive, GitCompare, Pencil, Search } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { GcodeViewer, type GcodeViewerHandle, type LineMark } from "@/components/GcodeViewer";
import { JobStatusBadge, SeverityIcon, ValidationBadge, VerificationBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Select, Textarea } from "@/components/ui/form";
import { Alert, Dialog, DialogContent, KV, Skeleton, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui/misc";
import { api, download } from "@/lib/api";
import type { CamJob, Program, Revision } from "@/lib/types";
import { fmtDate, fmt } from "@/lib/utils";
import { ReviewPanel } from "./ReviewPanel";
import { SimulationPanel } from "./SimulationPanel";
import { ValidationPanel } from "./ValidationPanel";

export function ProgramWorkspace({ programId, initialRevisionId, defaultTab = "program" }: { programId: string; initialRevisionId?: string; defaultTab?: string }) {
  const qc = useQueryClient();
  const prog = useQuery({ queryKey: ["program", programId], queryFn: () => api<Program>(`/api/programs/${programId}`) });
  const [revId, setRevId] = useState<string | undefined>(initialRevisionId);
  const currentRevId = revId ?? prog.data?.revisions[prog.data.revisions.length - 1]?.id;
  const rev = useQuery({ queryKey: ["revision", currentRevId], queryFn: () => api<Revision>(`/api/program-revisions/${currentRevId}`), enabled: Boolean(currentRevId) });
  const [tab, setTab] = useState(defaultTab);
  const viewer = useRef<GcodeViewerHandle>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [diffAgainst, setDiffAgainst] = useState<string>("");
  const [dlError, setDlError] = useState<string | null>(null);
  const save = (path: string, name: string) => {
    setDlError(null);
    download(path, name).catch((e: Error) => setDlError(e.message));
  };

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["program", programId] });
    qc.invalidateQueries({ queryKey: ["revision"] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const revalidate = useMutation({ mutationFn: () => api(`/api/program-revisions/${currentRevId}/validate`, { method: "POST" }), onSuccess: refresh });
  const goToLine = (l: number) => {
    setTab("program");
    setTimeout(() => viewer.current?.goToLine(l), 50);
  };

  const marks = useMemo<LineMark[]>(() => {
    const r = rev.data;
    if (!r) return [];
    const m: LineMark[] = r.operation_map.map((o) => ({ line: o.start_line, kind: "op" }));
    for (const c of r.validation?.checks ?? []) for (const l of c.lines ?? []) m.push({ line: l, kind: c.severity === "error" ? "error" : "warn" });
    return m;
  }, [rev.data]);

  if (prog.isLoading || !prog.data) return <Skeleton className="h-[600px]" />;
  const p = prog.data;
  const r = rev.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-base font-semibold">
              {p.program_name} <JobStatusBadge status={r?.status ?? p.status} />
              {r?.released && <Badge tone="ok">Released</Badge>}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              <span>{p.design_filename}</span>
              <span>
                Post {p.postprocessor.key} {p.postprocessor.version} <VerificationBadge status={p.postprocessor.status} />
              </span>
              <span>
                {p.machine_configuration.name} <VerificationBadge status={p.machine_configuration.verification_status} />
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Select value={currentRevId} onChange={(e) => setRevId(e.target.value)} className="h-8 w-auto text-xs" aria-label="Revision">
              {p.revisions.map((x) => (
                <option key={x.id} value={x.id}>
                  R{x.revision_number} · {x.source === "manual_edit" ? "edited" : "generated"}
                  {x.released ? " · released" : ""}
                </option>
              ))}
            </Select>
            {r?.validation && <ValidationBadge status={r.validation.status} />}
          </div>
        </CardContent>
      </Card>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="program">Program</TabsTrigger>
          <TabsTrigger value="validation">Validation</TabsTrigger>
          <TabsTrigger value="simulation">Simulation</TabsTrigger>
          <TabsTrigger value="review">Review & release</TabsTrigger>
          <TabsTrigger value="revisions">Revisions</TabsTrigger>
          <TabsTrigger value="config">Configuration</TabsTrigger>
        </TabsList>
        {!r ? (
          <Skeleton className="h-[500px]" />
        ) : (
          <>
            <TabsContent value="program">
              <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
                <Card className="overflow-hidden">
                  <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
                    <div className="text-xs text-muted">
                      {r.line_count} lines · {r.file_extension} · SHA-256 <span className="font-mono">{r.sha256.slice(0, 16)}…</span>
                    </div>
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="ghost" onClick={() => viewer.current?.openSearch()}>
                        <Search /> Search
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(r.content ?? "")}>
                        <Copy /> Copy
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => save(`/api/program-revisions/${r.id}/download`, `program${r.file_extension}`)} data-testid="download-nc">
                        <Download /> NC file
                      </Button>
                      <Button size="sm" onClick={() => save(`/api/program-revisions/${r.id}/export`, "export.zip")} data-testid="export-bundle">
                        <FileArchive /> Export bundle
                      </Button>
                    </div>
                  </div>
                  <GcodeViewer ref={viewer} content={r.content ?? ""} marks={marks} className="h-[640px]" />
                </Card>
                <div className="space-y-4">
                  {dlError && <Alert tone="err" title="Download failed">{dlError}</Alert>}
                  {!r.released && (
                    <Alert tone="warn" title="Not released">
                      Downloads are review copies (file name ends in _UNRELEASED) until all release gates pass.
                    </Alert>
                  )}
                  <Card>
                    <CardHeader>
                      <CardTitle>Operations</CardTitle>
                    </CardHeader>
                    <div className="border-t border-border">
                      {r.operation_map.map((o, i) => (
                        <button key={o.operation_id} onClick={() => viewer.current?.goToLine(o.start_line)} className="flex w-full items-start justify-between gap-2 border-b border-border px-5 py-2 text-left text-sm last:border-0 hover:bg-surface-2/60">
                          <span>
                            <span className="text-muted">{i + 1}.</span> {o.name}
                          </span>
                          <span className="shrink-0 font-mono text-xs text-muted">
                            T{o.tool_number} · {o.start_line}
                          </span>
                        </button>
                      ))}
                      {!r.operation_map.length && <p className="px-5 py-3 text-sm text-muted">No operation markers (manually edited revision).</p>}
                    </div>
                  </Card>
                  <Card>
                    <CardHeader>
                      <CardTitle>Postprocessor notes</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      {r.post_warnings.map((w, i) => (
                        <div key={i} className="flex gap-2 text-xs">
                          <SeverityIcon severity="warning" className="size-3.5 shrink-0" />
                          {w.message}
                        </div>
                      ))}
                      {!r.post_warnings.length && <p className="text-sm text-muted">None.</p>}
                    </CardContent>
                  </Card>
                </div>
              </div>
            </TabsContent>
            <TabsContent value="validation">
              <ValidationPanel rev={r} onLine={goToLine} onRevalidate={() => revalidate.mutate()} revalidating={revalidate.isPending} />
            </TabsContent>
            <TabsContent value="simulation">
              <SimulationPanel revisionId={r.id} onLine={goToLine} />
            </TabsContent>
            <TabsContent value="review">
              <ReviewPanel rev={r} onChanged={refresh} />
            </TabsContent>
            <TabsContent value="revisions">
              <RevisionsPanel program={p} rev={r} onEdit={() => setEditOpen(true)} diffAgainst={diffAgainst} setDiffAgainst={setDiffAgainst} onSelect={setRevId} />
            </TabsContent>
            <TabsContent value="config">
              <ConfigPanel jobId={p.cam_job_id} />
            </TabsContent>
          </>
        )}
      </Tabs>
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        {r && editOpen && (
          <EditRevisionDialog
            rev={r}
            onDone={(newId) => {
              setEditOpen(false);
              refresh();
              if (newId) setRevId(newId);
            }}
          />
        )}
      </Dialog>
    </div>
  );
}

function EditRevisionDialog({ rev, onDone }: { rev: Revision; onDone: (id?: string) => void }) {
  const [content, setContent] = useState(rev.content ?? "");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api<Revision>(`/api/program-revisions/${rev.id}/revisions`, { method: "POST", json: { content, change_note: note } }),
    onSuccess: (n) => onDone(n.id),
  });
  return (
    <DialogContent title={`New revision from R${rev.revision_number}`} description="Revisions are immutable. Saving creates a new revision that is fully re-validated and must be reviewed again." className="w-[min(980px,calc(100vw-2rem))]">
      <Textarea value={content} onChange={(e) => setContent(e.target.value)} className="h-[50vh] font-mono text-xs" spellCheck={false} />
      <Field label="Change note" required className="mt-3">
        <Textarea value={note} onChange={(e) => setNote(e.target.value)} className="min-h-12" />
      </Field>
      {save.error && <Alert tone="err" className="mt-3">{(save.error as Error).message}</Alert>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onDone()}>
          Cancel
        </Button>
        <Button onClick={() => save.mutate()} disabled={note.trim().length < 3} loading={save.isPending}>
          Create revision
        </Button>
      </div>
    </DialogContent>
  );
}

function RevisionsPanel({ program, rev, onEdit, diffAgainst, setDiffAgainst, onSelect }: { program: Program; rev: Revision; onEdit: () => void; diffAgainst: string; setDiffAgainst: (s: string) => void; onSelect: (id: string) => void }) {
  const diff = useQuery({
    queryKey: ["diff", rev.id, diffAgainst],
    queryFn: () => api<{ diff: string; added: number; removed: number; from: number; to: number }>(`/api/program-revisions/${rev.id}/diff?against=${diffAgainst}`),
    enabled: Boolean(diffAgainst) && diffAgainst !== rev.id,
  });
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Revision history</CardTitle>
            <CardDescription>Generated output is never modified in place.</CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={onEdit}>
            <Pencil /> Edit as new revision
          </Button>
        </CardHeader>
        <Table>
          <thead>
            <tr>
              <Th>Rev</Th>
              <Th>Source</Th>
              <Th>Status</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {program.revisions.map((x) => (
              <tr key={x.id} className={x.id === rev.id ? "bg-accent-soft" : "cursor-pointer hover:bg-surface-2/60"} onClick={() => onSelect(x.id)}>
                <Td className="font-mono">R{x.revision_number}</Td>
                <Td>
                  {x.source === "manual_edit" ? "Manual edit" : "Generated"}
                  {x.change_note && <div className="text-xs text-muted">{x.change_note}</div>}
                </Td>
                <Td>
                  <JobStatusBadge status={x.status} />
                </Td>
                <Td className="text-muted">{fmtDate(x.created_at)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitCompare className="size-4" /> Compare R{rev.revision_number} with
          </CardTitle>
          <Select value={diffAgainst} onChange={(e) => setDiffAgainst(e.target.value)} className="h-8 w-40 text-xs">
            <option value="">select revision…</option>
            {program.revisions
              .filter((x) => x.id !== rev.id)
              .map((x) => (
                <option key={x.id} value={x.id}>
                  R{x.revision_number}
                </option>
              ))}
          </Select>
        </CardHeader>
        <CardContent>
          {diff.data ? (
            <>
              <div className="mb-2 text-xs text-muted">
                +{diff.data.added} / −{diff.data.removed} lines
              </div>
              <pre className="max-h-[520px] overflow-auto rounded-md bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">
                {diff.data.diff.split("\n").map((l, i) => (
                  <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "bg-ok-soft text-ok" : l.startsWith("-") && !l.startsWith("---") ? "bg-err-soft text-err" : l.startsWith("@@") ? "text-accent" : ""}>
                    {l || " "}
                  </div>
                ))}
              </pre>
            </>
          ) : (
            <p className="text-sm text-muted">{program.revisions.length < 2 ? "Only one revision exists." : "Select a revision to compare."}</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ConfigPanel({ jobId }: { jobId: string }) {
  const job = useQuery({ queryKey: ["job-full", jobId], queryFn: () => api<CamJob>(`/api/cam-jobs/${jobId}`), staleTime: Infinity });
  if (!job.data?.input_snapshot) return <Skeleton className="h-64" />;
  const s = job.data.input_snapshot;
  const mc = s.machine_configuration;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Exact production configuration</CardTitle>
            <CardDescription>Frozen snapshot used by the CAM job (SHA-256 {job.data.snapshot_sha256.slice(0, 16)}…).</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <KV
            cols={2}
            items={[
              ["Design", `${s.design.filename} R${s.design.revision}`],
              ["Design SHA-256", <span className="font-mono text-xs">{s.design.sha256.slice(0, 20)}…</span>],
              ["Geometry engine", `${s.geometry.engine} ${s.geometry.engine_version}`],
              ["CAM engine", `${job.data.engine} ${job.data.engine_version}`],
              ["Machine configuration", mc.name],
              ["Machine status", mc.verification_status],
              ["Controller", `${mc.controller.manufacturer} ${mc.controller.model} ${mc.controller.version}`],
              ["Postprocessor", `${s.postprocessor.key} ${s.postprocessor.version} (${s.postprocessor.status})`],
              ["Postprocessor SHA-256", <span className="font-mono text-xs">{s.postprocessor.config_sha256.slice(0, 20)}…</span>],
              ["Material", s.stock.material?.designation ?? "—"],
              ["Stock", `${fmt(s.stock.dimensions.x)} × ${fmt(s.stock.dimensions.y)} × ${fmt(s.stock.dimensions.z)} mm`],
              ["WCS / datum", `${s.setup.work_offset} · ${s.setup.datum}`],
              ["Orientation", `${s.setup.orientation} · rot Z ${s.setup.rot_z_deg}°`],
              ["Tolerance", `${s.setup.tolerance_mm} mm`],
              ["Workholding", `${s.workholding.kind} ${s.workholding.name}`],
              ["Coolant", s.setup.coolant],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Tools in this program</CardTitle>
        </CardHeader>
        <Table>
          <thead>
            <tr>
              <Th>T</Th>
              <Th>Name</Th>
              <Th>Ø</Th>
              <Th>Stick-out</Th>
            </tr>
          </thead>
          <tbody>
            {s.tools.map((t: any) => (
              <tr key={t.id}>
                <Td className="font-mono">T{t.tool_number}</Td>
                <Td>{t.name}</Td>
                <Td>{fmt(t.diameter_mm)}</Td>
                <Td>{t.assembly ? fmt(t.assembly.stick_out_mm) : "—"}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
