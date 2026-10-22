import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ToolForm } from "@/components/ToolForm";
import { ToolTable } from "@/components/ToolTable";
import { VerificationBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/form";
import { Alert, Dialog, DialogContent, EmptyState, KV, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Material, PostprocessorSummary, ToolLibrary } from "@/lib/types";
import { fmtDate, humanize } from "@/lib/utils";

export function Tools() {
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const libs = useQuery({ queryKey: ["tool-libraries"], queryFn: () => api<ToolLibrary[]>("/api/tool-libraries") });
  const materials = useQuery({ queryKey: ["materials"], queryFn: () => api<Material[]>("/api/materials") });
  const [adding, setAdding] = useState<string | null>(null);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api("/api/tool-libraries", { method: "POST", json: { name } }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["tool-libraries"] });
    },
  });
  const example = useMutation({ mutationFn: () => api("/api/tool-libraries/example", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["tool-libraries"] }) });
  const editable = hasRole("programmer");
  return (
    <>
      <PageHeader title="Tool library" description="Tools, holder assemblies and offsets used by the planner, postprocessor and collision checks. Values must match the physical tools." />
      {editable && (
        <Card className="mb-4">
          <CardContent className="flex flex-wrap items-end gap-3 pt-4">
            <Field label="New library name" className="w-72">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Button variant="outline" onClick={() => create.mutate()} disabled={!name.trim()} loading={create.isPending}>
              Create library
            </Button>
            <Button variant="ghost" onClick={() => example.mutate()} loading={example.isPending}>
              Create example library
            </Button>
          </CardContent>
        </Card>
      )}
      {libs.isLoading ? (
        <Skeleton className="h-64" />
      ) : !libs.data?.length ? (
        <EmptyState title="No tool libraries">Create a library and add your shop's tools, or start from the clearly labelled example library.</EmptyState>
      ) : (
        <div className="space-y-4">
          {libs.data.map((l) => (
            <Card key={l.id}>
              <CardHeader>
                <div>
                  <CardTitle>{l.name}</CardTitle>
                  {l.description && <CardDescription>{l.description}</CardDescription>}
                </div>
                {editable && (
                  <Button size="sm" onClick={() => setAdding(l.id)}>
                    <Plus /> Add tool
                  </Button>
                )}
              </CardHeader>
              <ToolTable library={l} editable={editable} />
            </Card>
          ))}
        </div>
      )}
      <Card className="mt-6">
        <CardHeader>
          <div>
            <CardTitle>Materials and cutting data</CardTitle>
            <CardDescription>Generic starting values from typical handbook ranges. They are labelled as estimates wherever they are used; tool-manufacturer data entered on a tool takes precedence.</CardDescription>
          </div>
        </CardHeader>
        <Table>
          <thead>
            <tr>
              <Th>Material</Th>
              <Th>Grade</Th>
              <Th>Standard / equivalents</Th>
              <Th>Condition</Th>
              <Th>Carbide Vc (m/min)</Th>
              <Th>Data confidence</Th>
            </tr>
          </thead>
          <tbody>
            {materials.data?.flatMap((m) =>
              m.grades.map((g: any) => (
                <tr key={g.id}>
                  <Td>
                    {m.name} <span className="text-muted">ISO {m.iso_group}</span>
                  </Td>
                  <Td className="font-medium">{g.designation}</Td>
                  <Td className="text-xs">
                    {g.standard} {g.equivalents?.length ? `· ${g.equivalents.join(", ")}` : ""}
                  </Td>
                  <Td className="text-xs">
                    {g.condition} {g.hardness && `· ${g.hardness}`}
                  </Td>
                  <Td>{g.cutting_data?.carbide ? g.cutting_data.carbide.vc_m_min.join("–") : <span className="text-warn">none (enter manufacturer data)</span>}</Td>
                  <Td className="text-xs text-warn">{humanize(g.cutting_data_confidence)}</Td>
                </tr>
              )),
            )}
          </tbody>
        </Table>
      </Card>
      <Dialog open={Boolean(adding)} onOpenChange={(o) => !o && setAdding(null)}>
        {adding && (
          <DialogContent title="Add tool" className="w-[min(820px,calc(100vw-2rem))]">
            <ToolForm libraryId={adding} onDone={() => setAdding(null)} />
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}

export function Postprocessors() {
  const list = useQuery({ queryKey: ["posts"], queryFn: () => api<PostprocessorSummary[]>("/api/catalogue/postprocessors") });
  return (
    <>
      <PageHeader title="Postprocessors" description="Versioned, immutable, content-hashed controller configurations. Status changes only through recorded verification." />
      <Card>
        <Table>
          <thead>
            <tr>
              <Th>Postprocessor</Th>
              <Th>Version</Th>
              <Th>Controller family</Th>
              <Th>Dialect / file</Th>
              <Th>Status</Th>
              <Th>Config SHA-256</Th>
            </tr>
          </thead>
          <tbody>
            {list.data?.map((p) => (
              <tr key={p.id} className="hover:bg-surface-2/60">
                <Td>
                  <Link to={`/postprocessors/${p.id}`} className="font-medium text-accent hover:underline">
                    {p.name}
                  </Link>
                  <div className="font-mono text-xs text-muted">{p.key}</div>
                </Td>
                <Td>{p.version}</Td>
                <Td>{p.controller_family}</Td>
                <Td>
                  {p.dialect} · {p.file_extension}
                </Td>
                <Td>
                  <VerificationBadge status={p.status} />
                </Td>
                <Td className="font-mono text-xs">{p.config_sha256.slice(0, 16)}…</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <Alert tone="info" className="mt-4" title="Verification states">
        Draft: entered, not reviewed · Documented: backed by identified documentation · Tested: output matches reviewed expected fixtures (software level) ·
        Machine verified: proven on a specific machine configuration · Deprecated: not for new jobs.
      </Alert>
    </>
  );
}

export function PostprocessorDetail() {
  const { postId } = useParams();
  const p = useQuery({ queryKey: ["post", postId], queryFn: () => api<PostprocessorSummary>(`/api/catalogue/postprocessors/${postId}`) });
  if (!p.data) return <Skeleton className="h-96" />;
  const d = p.data;
  return (
    <>
      <PageHeader eyebrow={<Link to="/postprocessors">Postprocessors</Link>} title={`${d.name} ${d.version}`} description={d.config?.description} actions={<VerificationBadge status={d.status} />} />
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Summary</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <KV
                cols={2}
                items={[
                  ["Key", d.key],
                  ["Dialect", d.dialect],
                  ["File extension", d.file_extension],
                  ["License", d.license],
                  ["Supported G-codes", d.config?.supported_gcodes.join(" ")],
                  ["Supported M-codes", d.config?.supported_mcodes.join(" ")],
                  ["Program end", d.config?.program.end_word],
                  ["Canned cycles", d.config?.canned_cycles.enabled ? "enabled" : "expanded to linear moves"],
                ]}
              />
              <div>
                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Known limitations</div>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {d.known_limitations.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
              {d.documentation_refs.length > 0 && (
                <div>
                  <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Documentation</div>
                  {d.documentation_refs.map((r) => (
                    <a key={r} href={r} target="_blank" rel="noreferrer" className="block truncate text-sm text-accent hover:underline">
                      {r}
                    </a>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Verification record</CardTitle>
            </CardHeader>
            <Table>
              <thead>
                <tr>
                  <Th>Level</Th>
                  <Th>Method</Th>
                  <Th>Result</Th>
                  <Th>Reviewer</Th>
                  <Th>Date</Th>
                </tr>
              </thead>
              <tbody>
                {d.verifications?.map((v) => (
                  <tr key={v.id}>
                    <Td>
                      <VerificationBadge status={v.level} />
                    </Td>
                    <Td>
                      {humanize(v.method)}
                      <div className="text-xs text-muted">{v.notes}</div>
                    </Td>
                    <Td className={v.result === "passed" ? "text-ok" : "text-err"}>{v.result}</Td>
                    <Td>{v.reviewer_name}</Td>
                    <Td className="text-muted">{fmtDate(v.verified_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Configuration (immutable)</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-[720px] overflow-auto rounded-md bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{JSON.stringify(d.config, null, 2)}</pre>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
