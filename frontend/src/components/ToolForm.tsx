import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, NumberInput, Select } from "@/components/ui/form";
import { Alert } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Tool } from "@/lib/types";

export const TOOL_TYPES = [
  ["flat_endmill", "Flat end mill"],
  ["bull_endmill", "Bull-nose end mill"],
  ["ball_endmill", "Ball end mill"],
  ["face_mill", "Face mill"],
  ["drill", "Twist drill"],
  ["spot_drill", "Spot / centre drill"],
  ["chamfer_mill", "Chamfer mill"],
  ["reamer", "Reamer"],
  ["tap", "Tap"],
  ["thread_mill", "Thread mill"],
];

type Num = number | null;

export function ToolForm({ libraryId, tool, onDone }: { libraryId: string; tool?: Tool; onDone: () => void }) {
  const qc = useQueryClient();
  const [t, setT] = useState({
    tool_number: tool?.tool_number ?? null as Num,
    name: tool?.name ?? "",
    tool_type: tool?.tool_type ?? "flat_endmill",
    diameter_mm: tool?.diameter_mm ?? null as Num,
    cutting_length_mm: tool?.cutting_length_mm ?? null as Num,
    overall_length_mm: tool?.overall_length_mm ?? null as Num,
    shank_diameter_mm: tool?.shank_diameter_mm ?? null as Num,
    flute_count: tool?.flute_count ?? null as Num,
    corner_radius_mm: tool?.corner_radius_mm ?? 0 as Num,
    tip_angle_deg: tool?.tip_angle_deg ?? null as Num,
    tool_material: tool?.tool_material ?? "carbide",
    coating: tool?.coating ?? "",
    manufacturer: tool?.manufacturer ?? "",
    product_code: tool?.product_code ?? "",
    length_offset_number: tool?.length_offset_number ?? null as Num,
    condition: tool?.condition ?? "new",
  });
  const [asm, setAsm] = useState({
    holder_name: tool?.assembly?.holder_name ?? "",
    holder_diameter_mm: tool?.assembly?.holder_diameter_mm ?? null as Num,
    holder_length_mm: tool?.assembly?.holder_length_mm ?? null as Num,
    stick_out_mm: tool?.assembly?.stick_out_mm ?? null as Num,
  });
  const save = useMutation({
    mutationFn: () => {
      const hasAsm = asm.holder_diameter_mm && asm.holder_length_mm && asm.stick_out_mm;
      const body = { ...t, assembly: hasAsm ? asm : null, cutting_data: tool?.cutting_data ?? {} };
      return tool
        ? api(`/api/tools/${tool.id}`, { method: "PUT", json: body })
        : api(`/api/tool-libraries/${libraryId}/tools`, { method: "POST", json: body });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tool-libraries"] });
      qc.invalidateQueries({ queryKey: ["setup"] });
      onDone();
    },
  });
  const n = <K extends keyof typeof t>(k: K) => (v: Num) => setT({ ...t, [k]: v });
  const drill = ["drill", "spot_drill"].includes(t.tool_type);
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid grid-cols-3 gap-3">
        <Field label="Tool number (T)" required>
          <NumberInput value={t.tool_number} onChange={n("tool_number")} step={1} required />
        </Field>
        <Field label="Name" required className="col-span-2">
          <Input value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} required />
        </Field>
        <Field label="Type" required>
          <Select value={t.tool_type} onChange={(e) => setT({ ...t, tool_type: e.target.value })}>
            {TOOL_TYPES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Tool material">
          <Select value={t.tool_material} onChange={(e) => setT({ ...t, tool_material: e.target.value })}>
            <option value="carbide">Carbide</option>
            <option value="hss">HSS</option>
            <option value="hss_co">HSS-Co</option>
            <option value="insert">Indexable insert</option>
          </Select>
        </Field>
        <Field label="Coating">
          <Input value={t.coating} onChange={(e) => setT({ ...t, coating: e.target.value })} />
        </Field>
        <Field label="Diameter" unit="mm" required>
          <NumberInput value={t.diameter_mm} onChange={n("diameter_mm")} required />
        </Field>
        <Field label="Cutting (flute) length" unit="mm" required>
          <NumberInput value={t.cutting_length_mm} onChange={n("cutting_length_mm")} required />
        </Field>
        <Field label="Overall length" unit="mm" required>
          <NumberInput value={t.overall_length_mm} onChange={n("overall_length_mm")} required />
        </Field>
        <Field label="Flutes" required>
          <NumberInput value={t.flute_count} onChange={n("flute_count")} step={1} required />
        </Field>
        <Field label="Shank diameter" unit="mm">
          <NumberInput value={t.shank_diameter_mm} onChange={n("shank_diameter_mm")} />
        </Field>
        {drill ? (
          <Field label="Point angle" unit="°" required>
            <NumberInput value={t.tip_angle_deg} onChange={n("tip_angle_deg")} required />
          </Field>
        ) : (
          <Field label="Corner radius" unit="mm">
            <NumberInput value={t.corner_radius_mm} onChange={n("corner_radius_mm")} />
          </Field>
        )}
        <Field label="Length offset (H)" hint="Defaults to the tool number">
          <NumberInput value={t.length_offset_number} onChange={n("length_offset_number")} step={1} />
        </Field>
        <Field label="Manufacturer">
          <Input value={t.manufacturer} onChange={(e) => setT({ ...t, manufacturer: e.target.value })} />
        </Field>
        <Field label="Product code">
          <Input value={t.product_code} onChange={(e) => setT({ ...t, product_code: e.target.value })} />
        </Field>
      </div>
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Holder assembly (used for reach and holder-collision checks)</div>
        <div className="grid grid-cols-4 gap-3">
          <Field label="Holder">
            <Input value={asm.holder_name} onChange={(e) => setAsm({ ...asm, holder_name: e.target.value })} />
          </Field>
          <Field label="Holder Ø" unit="mm">
            <NumberInput value={asm.holder_diameter_mm} onChange={(v) => setAsm({ ...asm, holder_diameter_mm: v })} />
          </Field>
          <Field label="Holder length" unit="mm">
            <NumberInput value={asm.holder_length_mm} onChange={(v) => setAsm({ ...asm, holder_length_mm: v })} />
          </Field>
          <Field label="Stick-out" unit="mm">
            <NumberInput value={asm.stick_out_mm} onChange={(v) => setAsm({ ...asm, stick_out_mm: v })} />
          </Field>
        </div>
      </div>
      {save.error && <Alert tone="err">{(save.error as Error).message}</Alert>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          {tool ? "Save tool" : "Add tool"}
        </Button>
      </div>
    </form>
  );
}
