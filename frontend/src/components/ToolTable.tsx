import { Pencil } from "lucide-react";
import { useState } from "react";
import { ToolForm, TOOL_TYPES } from "@/components/ToolForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, Table, Td, Th } from "@/components/ui/misc";
import type { Tool, ToolLibrary } from "@/lib/types";
import { fmt } from "@/lib/utils";

export function ToolTable({ library, editable, limits }: { library: ToolLibrary; editable: boolean; limits?: Record<string, any> }) {
  const [edit, setEdit] = useState<Tool | null>(null);
  const tc = limits?.tool_changer ?? {};
  const label = Object.fromEntries(TOOL_TYPES);
  return (
    <>
      <Table>
        <thead>
          <tr>
            <Th>T</Th>
            <Th>H</Th>
            <Th>Name</Th>
            <Th>Type</Th>
            <Th>Ø mm</Th>
            <Th>Flute / OAL</Th>
            <Th>Z</Th>
            <Th>Material</Th>
            <Th>Holder · stick-out</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {library.tools.map((t) => {
            const tooBig = tc.max_tool_diameter_mm && t.diameter_mm > tc.max_tool_diameter_mm;
            return (
              <tr key={t.id} className={!t.active ? "opacity-50" : undefined}>
                <Td className="font-mono font-semibold">T{t.tool_number}</Td>
                <Td className="font-mono text-muted">H{t.length_offset_number ?? t.tool_number}</Td>
                <Td>
                  <div className="font-medium">{t.name}</div>
                  {t.limitations && <div className="text-xs text-warn">{t.limitations}</div>}
                </Td>
                <Td>{label[t.tool_type] ?? t.tool_type}</Td>
                <Td className={tooBig ? "text-err" : undefined}>{fmt(t.diameter_mm)}</Td>
                <Td>
                  {fmt(t.cutting_length_mm)} / {fmt(t.overall_length_mm)}
                </Td>
                <Td>{t.flute_count}</Td>
                <Td>
                  {t.tool_material}
                  {t.coating && <span className="text-muted"> · {t.coating}</span>}
                </Td>
                <Td>{t.assembly ? `${t.assembly.holder_name || "holder"} Ø${fmt(t.assembly.holder_diameter_mm)} · ${fmt(t.assembly.stick_out_mm)}` : <Badge tone="warn">no holder data</Badge>}</Td>
                <Td className="text-right">
                  {editable && (
                    <Button size="icon" variant="ghost" onClick={() => setEdit(t)} aria-label={`Edit T${t.tool_number}`}>
                      <Pencil />
                    </Button>
                  )}
                </Td>
              </tr>
            );
          })}
          {library.tools.length === 0 && (
            <tr>
              <Td colSpan={10} className="py-8 text-center text-muted">
                No tools in this library.
              </Td>
            </tr>
          )}
        </tbody>
      </Table>
      <Dialog open={Boolean(edit)} onOpenChange={(o) => !o && setEdit(null)}>
        {edit && (
          <DialogContent title={`Edit T${edit.tool_number}`} className="w-[min(820px,calc(100vw-2rem))]">
            <ToolForm libraryId={library.id} tool={edit} onDone={() => setEdit(null)} />
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}
