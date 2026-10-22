import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Workspace3D } from "@/components/viewer/Workspace3D";
import { JobStatusBadge, SeverityIcon } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Select } from "@/components/ui/form";
import { Alert, KV, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Design, Feature, Mesh } from "@/lib/types";
import { cn, fmt, humanize } from "@/lib/utils";

const UNITS = ["mm", "inch", "cm", "m", "ft", "um"];

function keyMeasure(f: Feature): string {
  const m = f.measurements;
  switch (f.type) {
    case "hole":
      return `Ø${fmt(m.diameter)} × ${fmt(m.depth)}${f.counterbore ? ` · CB Ø${fmt(f.counterbore.diameter)}×${fmt(f.counterbore.depth)}` : ""}`;
    case "pocket":
    case "slot":
    case "step":
      return `${fmt(m.length, 2)} × ${fmt(m.width, 2)} × ${fmt(m.depth, 2)} deep${m.internal_corner_radii?.length ? ` · R${m.internal_corner_radii.join("/")}` : ""}`;
    case "external_contour":
      return `perimeter ${fmt(m.perimeter, 1)} · height ${fmt(m.height)}`;
    case "top_face":
      return `Z${fmt(f.geometry.z)} · ${fmt(m.area, 0)} mm²`;
    case "pattern":
      return `${f.subtype} · Ø${fmt(m.diameter)}${m.pitch ? ` · pitch ${fmt(m.pitch)}` : ""}${m.pcd ? ` · PCD ${fmt(m.pcd)}` : ""}`;
    case "cylindrical_boss":
      return `Ø ${m.diameters?.map((d: number) => fmt(d)).join(", ")}`;
    default:
      return Object.entries(m)
        .slice(0, 2)
        .map(([k, v]) => `${humanize(k)} ${Array.isArray(v) ? v.join(",") : fmt(v as number)}`)
        .join(" · ");
  }
}

export function GeometryReview({ designId, footer }: { designId: string; footer?: (d: Design) => React.ReactNode }) {
  const qc = useQueryClient();
  const design = useQuery({
    queryKey: ["design", designId],
    queryFn: () => api<Design>(`/api/designs/${designId}`),
    refetchInterval: (q) => (["uploaded", "analyzing"].includes(q.state.data?.status ?? "") ? 1500 : false),
  });
  const an = design.data?.latest_analysis;
  const mesh = useQuery({
    queryKey: ["mesh", designId, an?.id],
    queryFn: () => api<Mesh>(`/api/designs/${designId}/mesh?analysis_id=${an!.id}`),
    enabled: Boolean(an?.status === "completed"),
    staleTime: Infinity,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [units, setUnits] = useState<string>("");
  const confirm = useMutation({
    mutationFn: (u: string) => api<Design>(`/api/designs/${designId}/units`, { method: "POST", json: { units: u } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["design", designId] }),
  });
  const feat = an?.features.find((f) => f.id === selected) ?? null;
  const highlight = useMemo(() => {
    if (!feat) return undefined;
    const refs = [...(feat.face_refs ?? []), ...((feat as any).wall_face_refs ?? [])];
    let ids = refs.map((r: any) => r.index).filter((x: unknown) => typeof x === "number");
    if (feat.type === "pattern") {
      const members = new Set<string>(feat.geometry.member_ids ?? []);
      ids = an!.features.filter((f) => members.has(f.id)).flatMap((f) => f.face_refs.map((r: any) => r.index));
    }
    return new Set<number>(ids);
  }, [feat, an]);

  if (design.isLoading || !design.data) return <Skeleton className="h-[520px]" />;
  const d = design.data;
  const s = an?.summary ?? {};
  const analyzing = ["uploaded", "analyzing"].includes(d.status) || an?.status === "pending" || an?.status === "running";

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <Card className="overflow-hidden">
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                {d.original_filename} <JobStatusBadge status={d.status} />
              </CardTitle>
              <CardDescription>
                {d.file_format.toUpperCase()} · revision {d.revision} · SHA-256 {d.sha256.slice(0, 12)}… ·{" "}
                {an?.representation === "mesh" ? "triangle mesh" : an?.representation === "drawing2d" ? "2D drawing" : "B-rep solid model"}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {analyzing ? (
              <div className="grid h-[460px] place-items-center rounded-lg border border-border bg-surface-2 text-sm text-muted">
                Parsing and analysing geometry in the isolated worker…
              </div>
            ) : d.status === "failed" ? (
              <Alert tone="err" title="Geometry could not be processed">
                {d.status_message || an?.error}
              </Alert>
            ) : mesh.data ? (
              <Workspace3D mesh={mesh.data} highlightFaces={highlight} className="h-[460px]" />
            ) : (
              <Skeleton className="h-[460px]" />
            )}
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Dimensions and units</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {an?.status === "completed" && (
                <KV
                  cols={3}
                  items={[
                    ["Size X", `${fmt(s.size?.[0])} mm`],
                    ["Size Y", `${fmt(s.size?.[1])} mm`],
                    ["Size Z", s.size?.[2] !== undefined ? `${fmt(s.size?.[2])} mm` : "—"],
                    ["Volume", s.volume_mm3 ? `${fmt(s.volume_mm3 / 1000, 2)} cm³` : "—"],
                    ["Surface area", s.surface_area_mm2 ? `${fmt(s.surface_area_mm2, 0)} mm²` : "—"],
                    ["Solids / faces", s.solid_count !== null && s.solid_count !== undefined ? `${s.solid_count} / ${s.face_count}` : s.triangle_count ? `${s.triangle_count} triangles` : "—"],
                    ["Valid B-rep", s.is_valid === undefined ? "n/a" : s.is_valid ? "yes" : "NO"],
                    ["2.5D prismatic", s.is_prismatic_2_5d === null || s.is_prismatic_2_5d === undefined ? "n/a" : s.is_prismatic_2_5d ? "yes" : "no"],
                    ["Orientation", s.orientation ? `${s.orientation.up} up` : "model"],
                  ]}
                />
              )}
              <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface-2/60 p-3">
                <div className="min-w-0 flex-1 text-sm">
                  <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Units</div>
                  <div>
                    Declared in file: <b>{d.detected_units ?? "none"}</b>
                    {d.confirmed_units && (
                      <>
                        {" "}
                        · confirmed: <b>{d.confirmed_units}</b>
                      </>
                    )}
                  </div>
                </div>
                <Field label="Confirm or correct" className="w-36">
                  <Select value={units || d.confirmed_units || d.detected_units || ""} onChange={(e) => setUnits(e.target.value)} data-testid="units-select">
                    <option value="" disabled>
                      select…
                    </option>
                    {UNITS.map((u) => (
                      <option key={u}>{u}</option>
                    ))}
                  </Select>
                </Field>
                <Button
                  variant="outline"
                  loading={confirm.isPending}
                  disabled={!(units || d.detected_units)}
                  onClick={() => confirm.mutate(units || d.detected_units!)}
                >
                  Confirm units
                </Button>
              </div>
              {confirm.error && <p className="text-sm text-err">{(confirm.error as Error).message}</p>}
            </CardContent>
          </Card>
          {an && (an.warnings.length > 0 || an.limitations.length > 0) && (
            <Card>
              <CardHeader>
                <CardTitle>Warnings and limitations</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {an.warnings.map((w, i) => (
                  <Alert key={i} tone={w.severity === "error" ? "err" : "warn"} title={w.code}>
                    {w.message}
                  </Alert>
                ))}
                <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
                  {an.limitations.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
      {an?.status === "completed" && an.features.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Detected features ({an.features.length})</CardTitle>
                <CardDescription>
                  Deterministic B-rep recognition ({an.engine_version}, {an.duration_ms} ms). Select a row to highlight it. Detection
                  does not imply machinability.
                </CardDescription>
              </div>
            </CardHeader>
            <Table>
              <thead>
                <tr>
                  <Th>ID</Th>
                  <Th>Type</Th>
                  <Th>Key dimensions (mm)</Th>
                  <Th>Access</Th>
                  <Th>Confidence</Th>
                </tr>
              </thead>
              <tbody>
                {an.features.map((f) => (
                  <tr
                    key={f.id}
                    onClick={() => setSelected(f.id === selected ? null : f.id)}
                    className={cn("cursor-pointer hover:bg-surface-2/60", selected === f.id && "bg-accent-soft")}
                  >
                    <Td className="font-mono text-xs">{f.id}</Td>
                    <Td>
                      <div className="font-medium">{humanize(f.type)}</div>
                      <div className="text-xs text-muted">{humanize(f.subtype)}</div>
                    </Td>
                    <Td className="text-xs">{keyMeasure(f)}</Td>
                    <Td>
                      {f.access_directions.length ? (
                        f.access_directions.map((a) => (
                          <Badge key={a} tone={a === "+Z" ? "neutral" : "warn"} className="mr-1">
                            {a}
                          </Badge>
                        ))
                      ) : (
                        <Badge tone="err">none</Badge>
                      )}
                    </Td>
                    <Td>{Math.round(f.confidence * 100)}%</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>{feat ? `Feature ${feat.id}: ${humanize(feat.type)}` : "Feature details"}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {!feat ? (
                <p className="text-muted">Select a feature to see its measurements, source rule and limitations.</p>
              ) : (
                <>
                  <KV
                    cols={2}
                    items={Object.entries(feat.measurements).map(([k, v]) => [
                      humanize(k),
                      Array.isArray(v) ? v.join(", ") || "—" : typeof v === "number" ? fmt(v) : String(v ?? "—"),
                    ])}
                  />
                  {feat.bottom && (
                    <p>
                      Bottom: <b>{feat.bottom.type}</b>
                      {feat.bottom.included_angle_deg ? ` (${feat.bottom.included_angle_deg}° included)` : ""}
                    </p>
                  )}
                  {feat.thread_hints?.map((h, i) => (
                    <Alert key={i} tone="info">
                      {h.message}
                    </Alert>
                  ))}
                  <div className="text-xs text-muted">
                    Source rule: <span className="font-mono">{feat.source}</span>
                  </div>
                  {feat.limitations.map((l, i) => (
                    <div key={i} className="flex gap-2 text-xs">
                      <SeverityIcon severity="warning" className="mt-0.5 size-3.5 shrink-0" />
                      {l}
                    </div>
                  ))}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
      {footer?.(d)}
    </div>
  );
}
