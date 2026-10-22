import { useQuery } from "@tanstack/react-query";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Workspace3D } from "@/components/viewer/Workspace3D";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { KV, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Mesh, Simulation } from "@/lib/types";
import { fmt, fmtDuration } from "@/lib/utils";

export function SimulationPanel({ revisionId, onLine }: { revisionId: string; onLine?: (line: number) => void }) {
  const sim = useQuery({ queryKey: ["sim", revisionId], queryFn: () => api<Simulation>(`/api/program-revisions/${revisionId}/simulation`), staleTime: Infinity });
  const mesh = useQuery({
    queryKey: ["mesh", sim.data?.design_id, (sim.data as any)?.analysis_id],
    queryFn: () => api<Mesh>(`/api/designs/${sim.data!.design_id}/mesh?analysis_id=${(sim.data as any).analysis_id}`),
    enabled: Boolean(sim.data),
    staleTime: Infinity,
  });
  const total = sim.data?.backplot?.kinds.length ?? 0;
  const [idx, setIdx] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [showResult, setShowResult] = useState(false);
  useEffect(() => {
    if (!playing) return;
    const step = Math.max(1, Math.round(total / 600));
    const h = setInterval(() => {
      setIdx((i) => {
        const n = Math.min(total, (i ?? 0) + step);
        if (n >= total) setPlaying(false);
        return n;
      });
    }, 30);
    return () => clearInterval(h);
  }, [playing, total]);
  const s = sim.data;
  const cur = idx === null ? null : Math.max(0, Math.min(total - 1, idx - 1));
  const line = cur !== null && s?.backplot ? s.backplot.lines[cur] : null;
  const op = useMemo(() => (line !== null && s ? s.operations.find((o) => line >= o.start_line && line <= o.end_line) : null), [line, s]);
  const tool = cur !== null && s?.backplot ? s.tools.find((t) => t.number === s.backplot!.tools[cur]) : null;
  const diam = useMemo(() => Object.fromEntries((s?.tools ?? []).map((t) => [t.number, t.diameter])), [s]);
  // part mesh is in the analysed (oriented) frame; the WCS translation is the matrix translation column
  const offset = s ? ([s.wcs_matrix[0][3], s.wcs_matrix[1][3], s.wcs_matrix[2][3]] as [number, number, number]) : undefined;

  if (sim.isLoading || !s) return <Skeleton className="h-[560px]" />;
  const st = s.stats;
  return (
    <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
      <Card className="overflow-hidden">
        <CardContent className="space-y-3 pt-4">
          <Workspace3D
            key={showResult ? "r" : "p"}
            mesh={mesh.data}
            meshOffset={offset}
            stock={s.stock_wcs}
            keepOut={s.keep_out_boxes}
            backplot={s.backplot}
            playIndex={idx}
            toolDiameters={diam}
            heightmap={s.heightmap}
            layers={{ heightmap: showResult, part: !showResult }}
            className="h-[500px]"
          />
          <div className="flex items-center gap-2">
            <Button size="icon" variant="outline" onClick={() => { setPlaying(false); setIdx(0); }} aria-label="Start">
              <SkipBack />
            </Button>
            <Button size="icon" onClick={() => { if (idx === null || idx >= total) setIdx(0); setPlaying(!playing); }} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause /> : <Play />}
            </Button>
            <Button size="icon" variant="outline" onClick={() => { setPlaying(false); setIdx(null); }} aria-label="Show all">
              <SkipForward />
            </Button>
            <input type="range" min={0} max={total} value={idx ?? total} onChange={(e) => { setPlaying(false); setIdx(Number(e.target.value)); }} className="flex-1 accent-[var(--accent)]" aria-label="Playback position" />
            <span className="w-28 text-right font-mono text-xs text-muted">
              {idx ?? total}/{total}
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="text-muted">
              {op ? (
                <>
                  <b className="text-foreground">{op.name}</b> · T{op.tool_number} {tool ? `${tool.name} Ø${fmt(tool.diameter)}` : ""} ·{" "}
                  <button className="font-mono text-accent hover:underline" onClick={() => line && onLine?.(line)}>
                    line {line}
                  </button>
                </>
              ) : (
                "Rapid moves: dashed orange · feed moves coloured by operation"
              )}
            </div>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={showResult} onChange={(e) => setShowResult(e.target.checked)} /> Show simulated stock result
            </label>
          </div>
        </CardContent>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Simulation results</CardTitle>
              <CardDescription>
                {s.engine} at {fmt(s.resolution_mm)} mm grid resolution
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <KV
              cols={2}
              items={[
                ["Estimated cycle time", fmtDuration(st.time?.total_s)],
                ["Feed / rapid time", `${fmtDuration(st.time?.feed_s)} / ${fmtDuration(st.time?.rapid_s)}`],
                ["Tool changes", st.time?.tool_changes],
                ["Feed path length", `${fmt(st.feed_length_mm / 1000, 2)} m`],
                ["Removed volume", `${fmt(st.removed_volume_mm3 / 1000, 1)} cm³`],
                ["Gouge cells", st.gouge_cells ?? "—"],
                ["Max remaining", st.max_remaining_mm !== undefined ? `${fmt(st.max_remaining_mm, 2)} mm` : "—"],
                ["Part surface in tolerance", st.part_surface_within_tolerance_pct !== undefined ? `${fmt(st.part_surface_within_tolerance_pct, 1)} %` : "—"],
              ]}
            />
            <div className="mt-4 text-xs text-muted">
              <div className="mb-1 font-medium text-foreground/80">Time-estimate assumptions</div>
              <ul className="list-disc space-y-0.5 pl-4">
                {(st.time?.assumptions ?? []).map((a: string, i: number) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>What this simulation does not prove</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
              {s.limitations.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
              <li>An animated toolpath is not evidence that the program is safe on the machine.</li>
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
