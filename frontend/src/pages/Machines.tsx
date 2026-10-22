import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { MachineCard } from "@/components/MachineCard";
import { VerificationBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Alert, Dialog, DialogContent, KV, PageHeader, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { MachineConfig, MachineModel, PostprocessorSummary } from "@/lib/types";
import { fmt } from "@/lib/utils";

interface Controller {
  id: string;
  manufacturer: string;
  name: string;
  family: string;
  versions: { id: string; version: string; notes: string }[];
}

export function Machines() {
  const { hasRole } = useAuth();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const list = useQuery({ queryKey: ["machines", q, cat], queryFn: () => api<MachineConfig[]>(`/api/catalogue/machine-configurations?include_inactive=true&q=${encodeURIComponent(q)}${cat ? `&category=${cat}` : ""}`) });
  const makers = useQuery({ queryKey: ["manufacturers"], queryFn: () => api<{ machine: { id: string; name: string; country: string }[]; controller: { id: string; name: string }[] }>("/api/catalogue/manufacturers") });
  const [sel, setSel] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["machine", sel], queryFn: () => api<MachineConfig>(`/api/catalogue/machine-configurations/${sel}`), enabled: Boolean(sel) });
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHeader
        title="Machine library"
        description="Physical machines, their installed controllers and the postprocessor required for each configuration are separate records with their own verification status."
        actions={
          hasRole("engineer") && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> Organisation configuration
            </Button>
          )
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <div className="space-y-3">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute top-2.5 left-2.5 size-4 text-muted" />
              <Input className="pl-8" placeholder="Manufacturer, model, controller…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <Select value={cat} onChange={(e) => setCat(e.target.value)} className="w-40">
              <option value="">All types</option>
              <option value="vmc">VMC</option>
              <option value="hmc">HMC</option>
              <option value="router">Router</option>
              <option value="lathe">Lathe</option>
              <option value="mill_turn">Mill-turn</option>
              <option value="5axis">5-axis</option>
            </Select>
          </div>
          {list.data?.map((m) => <MachineCard key={m.id} m={m} selected={sel === m.id} onSelect={() => setSel(m.id)} />)}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Manufacturers in the catalogue</CardTitle>
                <CardDescription>Listing a manufacturer is a research target, not a claim that its machines are supported.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5">
              {makers.data?.machine.map((m) => (
                <span key={m.id} className="rounded border border-border px-2 py-0.5 text-xs">
                  {m.name}
                  {m.country && <span className="text-muted"> · {m.country}</span>}
                </span>
              ))}
            </CardContent>
            <CardContent className="flex flex-wrap gap-1.5 pt-0">
              {makers.data?.controller.map((m) => (
                <span key={m.id} className="rounded border border-accent/20 bg-accent-soft px-2 py-0.5 text-xs text-accent">
                  {m.name}
                </span>
              ))}
            </CardContent>
          </Card>
        </div>
        <div className="xl:sticky xl:top-4 xl:self-start">{detail.data ? <MachineDetail m={detail.data} /> : <Card><CardContent className="pt-5 text-sm text-muted">Select a configuration to inspect its capabilities, limits, machine-specific blocks and verification record.</CardContent></Card>}</div>
      </div>
      <Dialog open={creating} onOpenChange={setCreating}>
        {creating && <CreateConfigDialog onDone={() => setCreating(false)} />}
      </Dialog>
    </>
  );
}

function MachineDetail({ m }: { m: MachineConfig }) {
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const active = useMutation({
    mutationFn: (v: boolean) => api(`/api/catalogue/machine-configurations/${m.id}/active`, { method: "POST", json: { is_active: v } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["machine"] }),
  });
  const mm = m.machine_model;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{m.name}</CardTitle>
          <CardDescription>
            {m.scope === "global" ? "Global catalogue (read-only; maintained by platform administrators)" : "Organisation configuration"}
          </CardDescription>
        </div>
        <VerificationBadge status={m.verification_status} />
      </CardHeader>
      <CardContent className="space-y-4">
        {m.is_example && <Alert tone="warn">{m.source}</Alert>}
        <KV
          cols={2}
          items={[
            ["Manufacturer", mm.manufacturer.name],
            ["Model / family", `${mm.model} · ${mm.family}`],
            ["Type / axes", `${mm.category.toUpperCase()} · ${mm.axis_count} axes`],
            ["Spindle taper", mm.spindle_taper || "—"],
            ["Travel X/Y/Z", `${fmt(m.effective_limits.travel_x_mm, 0)} / ${fmt(m.effective_limits.travel_y_mm, 0)} / ${fmt(m.effective_limits.travel_z_mm, 0)} mm`],
            ["Spindle", `${fmt(m.effective_limits.spindle_min_rpm, 0)}–${fmt(m.effective_limits.spindle_max_rpm, 0)} rpm · ${fmt(m.effective_limits.spindle_power_kw, 1)} kW`],
            ["Max feed / rapid", `${fmt(m.effective_limits.max_feed_mm_min, 0)} / ${fmt(m.effective_limits.rapid_rate_mm_min, 0)} mm/min`],
            ["Tool changer", JSON.stringify(m.effective_limits.tool_changer ?? {})],
            ["Controller", `${m.controller.manufacturer} ${m.controller.model}`],
            ["Controller version", m.controller.version],
            ["Postprocessor", m.postprocessor ? `${m.postprocessor.key} ${m.postprocessor.version}` : "none"],
            ["Operations", m.supported_operations.join(", ")],
            ["Source type", m.source_type],
            ["Verified by", m.verified_by ? `${m.verified_by} (${m.verified_at?.slice(0, 10)})` : "—"],
          ]}
        />
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Axes</div>
          <Table>
            <thead>
              <tr>
                <Th>Axis</Th>
                <Th>Direction</Th>
                <Th>Limits</Th>
              </tr>
            </thead>
            <tbody>
              {mm.axes.map((a) => (
                <tr key={a.name}>
                  <Td className="font-mono">{a.name}</Td>
                  <Td className="text-xs">{a.direction ?? "—"}</Td>
                  <Td>{a.min !== undefined ? `${a.min} … ${a.max}` : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Machine-specific blocks</div>
          <pre className="max-h-40 overflow-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]">{JSON.stringify(m.machine_blocks ?? {}, null, 2)}</pre>
        </div>
        {m.source && !m.is_example && <p className="whitespace-pre-line text-xs text-muted">{m.source}</p>}
        {m.scope === "organization" && hasRole("engineer") && (
          <Button variant="outline" size="sm" onClick={() => active.mutate(!m.is_active)} loading={active.isPending}>
            {m.is_active ? "Disable configuration" : "Enable configuration"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function CreateConfigDialog({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["machine-models"], queryFn: () => api<MachineModel[]>("/api/catalogue/machine-models") });
  const ctrls = useQuery({ queryKey: ["controllers"], queryFn: () => api<Controller[]>("/api/catalogue/controllers") });
  const posts = useQuery({ queryKey: ["posts"], queryFn: () => api<PostprocessorSummary[]>("/api/catalogue/postprocessors") });
  const [f, setF] = useState({ name: "", machine_model_id: "", controller_version_id: "", postprocessor_version_id: "", source: "", blocks: "{}", ops: "face,pocket,adaptive,contour,drill,helix" });
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => {
      let blocks: unknown;
      try {
        blocks = JSON.parse(f.blocks);
      } catch {
        throw new Error("Machine blocks must be valid JSON");
      }
      return api("/api/catalogue/machine-configurations", {
        method: "POST",
        json: {
          name: f.name, machine_model_id: f.machine_model_id, controller_version_id: f.controller_version_id,
          postprocessor_version_id: f.postprocessor_version_id || null, machine_blocks: blocks, source: f.source,
          source_type: "owner_supplied", supported_operations: f.ops.split(",").map((s) => s.trim()).filter(Boolean),
        },
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["machines"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <DialogContent title="New organisation machine configuration" description="Starts as draft. Machine-specific blocks must come from the machine owner or documentation; they are validated against the postprocessor's declared codes." className="w-[min(760px,calc(100vw-2rem))]">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name" required className="col-span-2">
          <Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label="Machine model" required>
          <Select value={f.machine_model_id} onChange={(e) => setF({ ...f, machine_model_id: e.target.value })}>
            <option value="">select…</option>
            {models.data?.map((m) => (
              <option key={m.id} value={m.id}>
                {m.manufacturer.name} {m.model}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Controller version" required hint="Platform admins add controller versions">
          <Select value={f.controller_version_id} onChange={(e) => setF({ ...f, controller_version_id: e.target.value })}>
            <option value="">select…</option>
            {ctrls.data?.flatMap((c) =>
              c.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {c.manufacturer} {c.name} — {v.version}
                </option>
              )),
            )}
          </Select>
        </Field>
        <Field label="Postprocessor version">
          <Select value={f.postprocessor_version_id} onChange={(e) => setF({ ...f, postprocessor_version_id: e.target.value })}>
            <option value="">none</option>
            {posts.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} {p.version} ({p.status})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Enabled operations">
          <Input value={f.ops} onChange={(e) => setF({ ...f, ops: e.target.value })} />
        </Field>
        <Field label='Machine blocks (JSON, e.g. {"end_parking": ["G53 G0 Z0"], "coolant": {"flood": "M8", "off": "M9"}, "declared_mcodes": ["M8","M9"]})' className="col-span-2">
          <Textarea value={f.blocks} onChange={(e) => setF({ ...f, blocks: e.target.value })} className="font-mono text-xs" />
        </Field>
        <Field label="Source of this information" required className="col-span-2" hint="e.g. machine owner, OEM manual section, CNC engineer name">
          <Input value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} />
        </Field>
      </div>
      {err && <Alert tone="err" className="mt-3">{err}</Alert>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!f.name || !f.machine_model_id || !f.controller_version_id}>
          Create draft configuration
        </Button>
      </div>
    </DialogContent>
  );
}
