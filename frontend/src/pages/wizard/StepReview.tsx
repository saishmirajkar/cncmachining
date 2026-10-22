import { useMutation } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { GeometryReview } from "@/components/GeometryReview";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Design, Setup } from "@/lib/types";

export function StepReview({ designId, setup }: { designId: string; setup?: Setup }) {
  const nav = useNavigate();
  const create = useMutation({
    mutationFn: (d: Design) => api<Setup>(`/api/projects/${d.project_id}/setups`, { method: "POST", json: { design_id: d.id, name: `Setup — ${d.original_filename}` } }),
    onSuccess: (s) => nav(`/jobs/${s.id}/machine`),
  });
  return (
    <GeometryReview
      designId={designId}
      footer={(d) => {
        const an = d.latest_analysis;
        const units = d.confirmed_units ?? d.detected_units;
        const camOk = an?.representation === "brep";
        return (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-4">
            <div className="min-w-0 flex-1">
              {d.status !== "analyzed" ? (
                <p className="text-sm text-muted">Waiting for the analysis to complete.</p>
              ) : !units ? (
                <Alert tone="warn" title="Units required">
                  This file declares no length unit. Confirm the unit before continuing.
                </Alert>
              ) : !camOk ? (
                <Alert tone="warn" title="CAM not available for this representation">
                  {an?.representation === "mesh" ? "STL meshes" : "2D DXF drawings"} can be inspected but cannot be used for toolpath
                  generation in this release. Provide a STEP, IGES or FreeCAD solid model.
                </Alert>
              ) : (
                <p className="text-sm text-muted">
                  Geometry is in <b>{units}</b>. Next, choose the machine configuration that will run this part.
                </p>
              )}
            </div>
            {setup ? (
              <Button onClick={() => nav(`/jobs/${setup.id}/machine`)}>
                Continue <ArrowRight />
              </Button>
            ) : (
              <Button onClick={() => create.mutate(d)} disabled={d.status !== "analyzed" || !units || !camOk} loading={create.isPending} data-testid="create-setup">
                Create setup and continue <ArrowRight />
              </Button>
            )}
          </div>
        );
      }}
    />
  );
}
