import { useMutation, useQuery } from "@tanstack/react-query";
import { Save, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Workspace3D } from "@/components/viewer/Workspace3D";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, NumberInput, Select, Textarea } from "@/components/ui/form";
import { Alert, KV, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Material, Mesh, Setup } from "@/lib/types";
import { fmt } from "@/lib/utils";
import { RequirementList, StepNav, usePatchSetup } from "./common";

type Num = number | null;
interface Form {
  orientation: string;
  rot_z_deg: number;
  material_grade_id: string;
  dims: { x: Num; y: Num; z: Num };
  offset: { x: Num; y: Num; z: Num };
  finishing_allowance_mm: Num;
  work_offset: string;
  datum: string;
  clearance_height_mm: Num;
  safe_height_mm: Num;
  wop: { x: Num; y: Num; z: Num };
  wh_kind: string;
  wh_name: string;
  wh: Record<string, Num | string>;
  clamping_restrictions: string;
  tolerance_mm: Num;
  surface_finish_ra_um: Num;
  max_stepdown_mm: Num;
  strategy_preference: string;
  cut_direction: string;
  coolant: string;
  production_quantity: Num;
  inspection_requirements: string;
}

function fromSetup(s: Setup): Form {
  return {
    orientation: s.orientation ?? "",
    rot_z_deg: s.rot_z_deg ?? 0,
    material_grade_id: s.stock?.material_grade_id ?? "",
    dims: { x: s.stock?.dimensions.x ?? null, y: s.stock?.dimensions.y ?? null, z: s.stock?.dimensions.z ?? null },
    offset: { x: s.stock?.part_offset.x ?? null, y: s.stock?.part_offset.y ?? null, z: s.stock?.part_offset.z ?? null },
    finishing_allowance_mm: s.stock?.finishing_allowance_mm ?? 0,
    work_offset: s.work_offset ?? "",
    datum: s.datum ?? "",
    clearance_height_mm: s.clearance_height_mm,
    safe_height_mm: s.safe_height_mm,
    wop: { x: s.work_offset_machine_position?.x ?? null, y: s.work_offset_machine_position?.y ?? null, z: s.work_offset_machine_position?.z ?? null },
    wh_kind: s.workholding?.kind ?? "",
    wh_name: s.workholding?.name ?? "",
    wh: { clamp_axis: "Y", ...(s.workholding?.parameters ?? {}) },
    clamping_restrictions: s.workholding?.clamping_restrictions ?? "",
    tolerance_mm: s.tolerance_mm,
    surface_finish_ra_um: s.surface_finish_ra_um,
    max_stepdown_mm: s.max_stepdown_mm,
    strategy_preference: s.strategy_preference,
    cut_direction: s.cut_direction,
    coolant: s.coolant ?? "",
    production_quantity: s.production_quantity,
    inspection_requirements: s.inspection_requirements,
  };
}

const ORIENT = [
  ["+Z", "Model +Z up (as designed)"],
  ["-Z", "Model −Z up (flipped)"],
  ["+X", "Model +X up"],
  ["-X", "Model −X up"],
  ["+Y", "Model +Y up"],
  ["-Y", "Model −Y up"],
];

export function StepSetup({ setup }: { setup: Setup }) {
  const [f, setF] = useState<Form>(() => fromSetup(setup));
  const [allow, setAllow] = useState<{ xy: Num; top: Num; bottom: Num }>({ xy: null, top: null, bottom: null });
  useEffect(() => setF(fromSetup(setup)), [setup.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const materials = useQuery({ queryKey: ["materials"], queryFn: () => api<Material[]>("/api/materials"), staleTime: Infinity });
  const mesh = useQuery({
    queryKey: ["mesh", setup.design_id, setup.analysis_id],
    queryFn: () => api<Mesh>(`/api/designs/${setup.design_id}/mesh?analysis_id=${setup.analysis_id}`),
    enabled: Boolean(setup.analysis_id),
    staleTime: Infinity,
  });
  const patch = usePatchSetup(setup.id);
  const suggest = useMutation({
    mutationFn: () =>
      api<{ dimensions: Form["dims"]; part_offset: Form["offset"] }>(`/api/setups/${setup.id}/stock-suggestion`, {
        method: "POST",
        json: { xy_allowance_mm: allow.xy, top_allowance_mm: allow.top, bottom_allowance_mm: allow.bottom },
      }),
    onSuccess: (r) => setF((p) => ({ ...p, dims: r.dimensions, offset: r.part_offset })),
  });
  const mc = setup.machine_configuration;
  const offsets: string[] = mc?.postprocessor?.config?.work_offsets ?? ["G54", "G55", "G56", "G57", "G58", "G59"];
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));

  function save() {
    const body: Record<string, unknown> = {
      tolerance_mm: f.tolerance_mm, surface_finish_ra_um: f.surface_finish_ra_um, max_stepdown_mm: f.max_stepdown_mm,
      strategy_preference: f.strategy_preference, cut_direction: f.cut_direction, inspection_requirements: f.inspection_requirements,
      production_quantity: f.production_quantity, clearance_height_mm: f.clearance_height_mm, safe_height_mm: f.safe_height_mm,
      rot_z_deg: f.rot_z_deg,
    };
    if (f.orientation) body.orientation = f.orientation;
    if (f.work_offset) body.work_offset = f.work_offset;
    if (f.datum) body.datum = f.datum;
    if (f.coolant) body.coolant = f.coolant;
    body.work_offset_machine_position = f.wop.x !== null && f.wop.y !== null && f.wop.z !== null ? f.wop : null;
    if (f.dims.x && f.dims.y && f.dims.z && f.offset.x !== null && f.offset.y !== null && f.offset.z !== null)
      body.stock = { shape: "block", dimensions: f.dims, part_offset: f.offset, material_grade_id: f.material_grade_id || null, finishing_allowance_mm: f.finishing_allowance_mm ?? 0 };
    if (f.wh_kind) {
      const params = Object.fromEntries(Object.entries(f.wh).filter(([, v]) => v !== null && v !== ""));
      body.workholding = { kind: f.wh_kind, name: f.wh_name, parameters: params, clamping_restrictions: f.clamping_restrictions };
    }
    patch.mutate(body);
  }

  const g = setup.geometry;
  const origin = g.origin_oriented;
  const offset3: [number, number, number] | undefined = origin ? [-origin[0], -origin[1], -origin[2]] : undefined;
  const fields = ["orientation", "stock", "workholding", "work_offset", "datum", "clearance_height_mm", "safe_height_mm", "coolant", "tolerance_mm", "design"];
  const analysisPending = setup.requirements.some((r) => r.field === "orientation" && r.message.includes("pending"));

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Workpiece orientation and material</CardTitle>
                <CardDescription>Which model direction points up the machine +Z axis. Features are re-analysed for the chosen orientation.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="Orientation" required>
                <Select value={f.orientation} onChange={(e) => set("orientation", e.target.value)} data-testid="orientation">
                  <option value="">select…</option>
                  {ORIENT.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Rotation about Z" unit="°">
                <Select value={f.rot_z_deg} onChange={(e) => set("rot_z_deg", Number(e.target.value))}>
                  {[0, 90, 180, 270].map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Material grade" required className="col-span-2" hint="Cutting data for listed grades are generic starting values, not manufacturer recommendations.">
                <Select value={f.material_grade_id} onChange={(e) => set("material_grade_id", e.target.value)} data-testid="material">
                  <option value="">select…</option>
                  {materials.data?.map((m) => (
                    <optgroup key={m.id} label={`${m.name} (ISO ${m.iso_group})`}>
                      {m.grades.map((gr) => (
                        <option key={gr.id} value={gr.id}>
                          {gr.designation} {gr.standard ? `— ${gr.standard}` : ""}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </Select>
              </Field>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Stock (block)</CardTitle>
                <CardDescription>Dimensions in the setup orientation. The part offset is the position of the part's bounding-box minimum corner inside the stock.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-4 items-end gap-3 rounded-md border border-dashed border-border-strong p-3">
                <Field label="XY allowance / side" unit="mm">
                  <NumberInput value={allow.xy} onChange={(v) => setAllow({ ...allow, xy: v })} />
                </Field>
                <Field label="Top allowance" unit="mm">
                  <NumberInput value={allow.top} onChange={(v) => setAllow({ ...allow, top: v })} />
                </Field>
                <Field label="Bottom allowance" unit="mm">
                  <NumberInput value={allow.bottom} onChange={(v) => setAllow({ ...allow, bottom: v })} />
                </Field>
                <Button variant="outline" onClick={() => suggest.mutate()} disabled={allow.xy === null || allow.top === null || allow.bottom === null || !setup.analysis_id} loading={suggest.isPending}>
                  <Wand2 /> Compute
                </Button>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {(["x", "y", "z"] as const).map((k) => (
                  <Field key={k} label={`Stock ${k.toUpperCase()}`} unit="mm" required>
                    <NumberInput value={f.dims[k]} onChange={(v) => set("dims", { ...f.dims, [k]: v })} data-testid={`stock-${k}`} />
                  </Field>
                ))}
                {(["x", "y", "z"] as const).map((k) => (
                  <Field key={k} label={`Part offset ${k.toUpperCase()}`} unit="mm" required>
                    <NumberInput value={f.offset[k]} onChange={(v) => set("offset", { ...f.offset, [k]: v })} />
                  </Field>
                ))}
                <Field label="Finishing allowance" unit="mm">
                  <NumberInput value={f.finishing_allowance_mm} onChange={(v) => set("finishing_allowance_mm", v)} />
                </Field>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Work coordinate system</CardTitle>
                <CardDescription>Heights are measured above the stock top. Front = operator side (−Y), left = −X.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="Work offset" required>
                <Select value={f.work_offset} onChange={(e) => set("work_offset", e.target.value)} data-testid="work-offset">
                  <option value="">select…</option>
                  {offsets.map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Datum (work origin)" required>
                <Select value={f.datum} onChange={(e) => set("datum", e.target.value)} data-testid="datum">
                  <option value="">select…</option>
                  {Object.entries(setup.datums).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Clearance height" unit="mm" required hint="Rapid moves between features">
                <NumberInput value={f.clearance_height_mm} onChange={(v) => set("clearance_height_mm", v)} data-testid="clearance" />
              </Field>
              <Field label="Safe height" unit="mm" required hint="Start of feed moves; must be below clearance">
                <NumberInput value={f.safe_height_mm} onChange={(v) => set("safe_height_mm", v)} data-testid="safe" />
              </Field>
              <div className="col-span-2">
                <div className="mb-1.5 text-xs font-medium text-foreground/80">Work offset position in machine coordinates (optional — enables absolute travel-limit checks)</div>
                <div className="grid grid-cols-3 gap-3">
                  {(["x", "y", "z"] as const).map((k) => (
                    <NumberInput key={k} placeholder={k.toUpperCase()} value={f.wop[k]} onChange={(v) => set("wop", { ...f.wop, [k]: v })} />
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Workholding</CardTitle>
                <CardDescription>Vise jaws become keep-out zones for collision checks and limit contour depth.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="Type" required>
                <Select value={f.wh_kind} onChange={(e) => set("wh_kind", e.target.value)} data-testid="workholding">
                  <option value="">select…</option>
                  <option value="vise">Machine vise</option>
                  <option value="fixture_plate">Fixture plate</option>
                  <option value="custom">Custom fixture</option>
                </Select>
              </Field>
              <Field label="Name / identifier">
                <input className="h-9 w-full rounded-md border border-border-strong px-2.5 text-sm" value={f.wh_name} onChange={(e) => set("wh_name", e.target.value)} />
              </Field>
              {f.wh_kind === "vise" &&
                (
                  [
                    ["jaw_width", "Jaw width"],
                    ["jaw_height", "Jaw height"],
                    ["jaw_thickness", "Jaw thickness"],
                    ["clamp_depth", "Clamped depth of stock"],
                  ] as const
                ).map(([k, l]) => (
                  <Field key={k} label={l} unit="mm" required>
                    <NumberInput value={(f.wh[k] as Num) ?? null} onChange={(v) => set("wh", { ...f.wh, [k]: v })} data-testid={`vise-${k}`} />
                  </Field>
                ))}
              {f.wh_kind === "vise" && (
                <Field label="Clamping axis">
                  <Select value={String(f.wh.clamp_axis ?? "Y")} onChange={(e) => set("wh", { ...f.wh, clamp_axis: e.target.value })}>
                    <option value="Y">Jaws on ±Y faces</option>
                    <option value="X">Jaws on ±X faces</option>
                  </Select>
                </Field>
              )}
              <Field label="Allowed breakthrough below stock" unit="mm" hint="Only if parallels/fixture are clear of through-holes">
                <NumberInput value={(f.wh.allowed_breakthrough_mm as Num) ?? null} onChange={(v) => set("wh", { ...f.wh, allowed_breakthrough_mm: v })} />
              </Field>
              <Field label="Clamping restrictions" className="col-span-2">
                <Textarea value={f.clamping_restrictions} onChange={(e) => set("clamping_restrictions", e.target.value)} />
              </Field>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Manufacturing requirements</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="Dimensional tolerance" unit="mm" required>
                <NumberInput value={f.tolerance_mm} onChange={(v) => set("tolerance_mm", v)} data-testid="tolerance" />
              </Field>
              <Field label="Surface finish target" unit="µm Ra">
                <NumberInput value={f.surface_finish_ra_um} onChange={(v) => set("surface_finish_ra_um", v)} />
              </Field>
              <Field label="Maximum stepdown" unit="mm" hint="Optional cap applied to every operation">
                <NumberInput value={f.max_stepdown_mm} onChange={(v) => set("max_stepdown_mm", v)} />
              </Field>
              <Field label="Coolant" required>
                <Select value={f.coolant} onChange={(e) => set("coolant", e.target.value)} data-testid="coolant">
                  <option value="">select…</option>
                  <option value="off">Off (dry)</option>
                  {(mc?.machine_model.coolant_options ?? []).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Strategy preference">
                <Select value={f.strategy_preference} onChange={(e) => set("strategy_preference", e.target.value)}>
                  <option value="conservative">Conservative</option>
                  <option value="balanced">Balanced</option>
                  <option value="productive">Productive</option>
                </Select>
              </Field>
              <Field label="Cut direction">
                <Select value={f.cut_direction} onChange={(e) => set("cut_direction", e.target.value)}>
                  <option value="climb">Climb</option>
                  <option value="conventional">Conventional</option>
                </Select>
              </Field>
              <Field label="Production quantity">
                <NumberInput value={f.production_quantity} onChange={(v) => set("production_quantity", v)} step={1} />
              </Field>
              <Field label="Inspection / probing requirements" className="col-span-2">
                <Textarea value={f.inspection_requirements} onChange={(e) => set("inspection_requirements", e.target.value)} />
              </Field>
            </CardContent>
          </Card>
        </div>
        <div className="space-y-4 xl:sticky xl:top-4 xl:self-start">
          <Card className="overflow-hidden">
            <CardHeader>
              <div>
                <CardTitle>Setup preview (WCS)</CardTitle>
                <CardDescription>Part, stock, datum and keep-out zones as saved.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {analysisPending ? (
                <div className="grid h-[380px] place-items-center rounded-lg border border-border bg-surface-2 text-sm text-muted">Analysing the selected orientation…</div>
              ) : mesh.data && offset3 ? (
                <Workspace3D mesh={mesh.data} meshOffset={offset3} stock={g.stock_wcs} keepOut={g.keep_out_boxes} className="h-[380px]" />
              ) : mesh.isLoading ? (
                <Skeleton className="h-[380px]" />
              ) : (
                <div className="grid h-[380px] place-items-center rounded-lg border border-border bg-surface-2 px-6 text-center text-sm text-muted">Save orientation, stock and datum to see the setup.</div>
              )}
              {g.stock_wcs && g.part_wcs && (
                <div className="mt-4">
                  <KV
                    cols={2}
                    items={[
                      ["Stock top (WCS Z)", fmt(g.stock_wcs.max[2])],
                      ["Part top / bottom (WCS Z)", `${fmt(g.part_wcs.max[2])} / ${fmt(g.part_wcs.min[2])}`],
                      ["Clearance / safe Z", `${fmt(g.clearance_z_wcs)} / ${fmt(g.safe_z_wcs)}`],
                      ["Vise jaw top Z", fmt(g.workholding_info?.jaw_top_z)],
                    ]}
                  />
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Open requirements</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <RequirementList items={setup.requirements} fields={fields} />
              {!setup.requirements.some((r) => fields.some((x) => r.field.startsWith(x))) && <Alert tone="ok">All setup requirements for this step are satisfied.</Alert>}
              {patch.error && <Alert tone="err">{(patch.error as Error).message}</Alert>}
              <Button onClick={save} loading={patch.isPending} className="w-full" data-testid="save-setup">
                <Save /> Save setup
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
      <StepNav setup={setup} current="stock" />
    </>
  );
}
