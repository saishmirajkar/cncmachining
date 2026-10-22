import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { Link, Navigate, useParams } from "react-router-dom";
import { JobStatusBadge } from "@/components/status";
import { Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { CamJob, Setup } from "@/lib/types";
import { cn } from "@/lib/utils";
import { StepCam } from "./StepCam";
import { StepMachine } from "./StepMachine";
import { StepPlan } from "./StepPlan";
import { StepProgram } from "./StepProgram";
import { StepReview } from "./StepReview";
import { StepSetup } from "./StepSetup";
import { StepTooling } from "./StepTooling";
import { StepUpload } from "./StepUpload";

export const STEPS = [
  { key: "upload", label: "Upload design" },
  { key: "geometry", label: "Review geometry" },
  { key: "machine", label: "Select machine" },
  { key: "stock", label: "Material & stock" },
  { key: "tooling", label: "Configure tooling" },
  { key: "plan", label: "Machining plan" },
  { key: "cam", label: "Generate toolpaths" },
  { key: "validate", label: "Validate & simulate" },
  { key: "export", label: "Inspect & export" },
] as const;
export type StepKey = (typeof STEPS)[number]["key"];

export function useSetup(setupId: string | undefined) {
  return useQuery({
    queryKey: ["setup", setupId],
    queryFn: () => api<Setup>(`/api/setups/${setupId}`),
    enabled: Boolean(setupId),
    refetchInterval: (q) => (q.state.data?.requirements.some((r) => r.message.includes("pending")) ? 2000 : false),
  });
}

export function useLatestJob(setup: Setup | undefined) {
  return useQuery({
    queryKey: ["jobs", "setup", setup?.id],
    enabled: Boolean(setup),
    queryFn: async () => (await api<CamJob[]>(`/api/cam-jobs?project_id=${setup!.project_id}`)).find((j) => j.setup_id === setup!.id) ?? null,
    refetchInterval: (q) => (["ready_for_cam", "cam_processing", "postprocessing"].includes(q.state.data?.status ?? "") ? 1500 : false),
  });
}

function stepDone(key: StepKey, s: Setup | undefined, job: CamJob | null | undefined): boolean {
  if (!s) return key === "upload";
  const missing = new Set(s.requirements.filter((r) => r.severity === "error").map((r) => r.field.split(".")[0]));
  switch (key) {
    case "upload":
    case "geometry":
      return true;
    case "machine":
      return Boolean(s.machine_configuration_id);
    case "stock":
      return Boolean(s.stock && s.workholding && s.datum && s.work_offset) && !missing.has("stock") && !missing.has("workholding");
    case "tooling":
      return Boolean(s.tool_library_id) && !missing.has("tool_library");
    case "plan":
      return s.plan_approved;
    case "cam":
      return Boolean(job?.program_ids.length);
    case "validate":
    case "export":
      return ["engineering_review_required", "ready_for_controlled_export"].includes(job?.status ?? "");
  }
}

export function Stepper({ current, setup, job, designId }: { current: StepKey; setup?: Setup; job?: CamJob | null; designId?: string }) {
  return (
    <ol className="mb-6 flex flex-wrap items-center gap-1 rounded-lg border border-border bg-surface p-1.5">
      {STEPS.map((s, i) => {
        const done = stepDone(s.key, setup, job);
        const active = s.key === current;
        const href = setup ? `/jobs/${setup.id}/${s.key}` : s.key === "upload" ? "/jobs/new" : designId ? `/jobs/new/design/${designId}` : undefined;
        const reachable = Boolean(setup) || s.key === "upload" || (s.key === "geometry" && designId);
        const content = (
          <span
            className={cn(
              "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
              active ? "bg-foreground text-background" : reachable ? "text-foreground/80 hover:bg-surface-2" : "text-muted/60",
            )}
          >
            <span
              className={cn(
                "grid size-5 place-items-center rounded-full text-[10px] font-semibold tabular",
                active ? "bg-background text-foreground" : done ? "bg-ok text-white" : "border border-border-strong",
              )}
            >
              {done && !active ? <Check className="size-3" /> : i + 1}
            </span>
            {s.label}
          </span>
        );
        return (
          <li key={s.key}>
            {reachable && href && !active ? <Link to={s.key === "geometry" && setup ? `/jobs/${setup.id}/geometry` : href}>{content}</Link> : content}
          </li>
        );
      })}
    </ol>
  );
}

export function WizardNew() {
  return (
    <>
      <WizardTitle />
      <Stepper current="upload" />
      <StepUpload />
    </>
  );
}

export function WizardDesign() {
  const { designId } = useParams();
  return (
    <>
      <WizardTitle />
      <Stepper current="geometry" designId={designId} />
      <StepReview designId={designId!} />
    </>
  );
}

function WizardTitle({ setup, job }: { setup?: Setup; job?: CamJob | null }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="text-xs font-medium text-muted">New CNC job</div>
        <h1 className="text-xl font-semibold tracking-tight">{setup ? setup.name : "Engineering design to CNC program"}</h1>
      </div>
      <div className="flex items-center gap-2">
        {setup && <JobStatusBadge status={job?.status ?? setup.status} />}
        {setup && (
          <Link to={`/projects/${setup.project_id}`} className="text-xs font-medium text-accent hover:underline">
            Open project
          </Link>
        )}
      </div>
    </div>
  );
}

export function WizardSetup() {
  const { setupId, step } = useParams();
  const setup = useSetup(setupId);
  const job = useLatestJob(setup.data);
  const key = (STEPS.find((s) => s.key === step)?.key ?? "machine") as StepKey;
  if (!STEPS.some((s) => s.key === step)) return <Navigate to={`/jobs/${setupId}/machine`} replace />;
  if (setup.isLoading || !setup.data) return <Skeleton className="h-96" />;
  const s = setup.data;
  return (
    <>
      <WizardTitle setup={s} job={job.data} />
      <Stepper current={key} setup={s} job={job.data} />
      {key === "upload" && <StepUpload />}
      {key === "geometry" && <StepReview designId={s.design_id} setup={s} />}
      {key === "machine" && <StepMachine setup={s} />}
      {key === "stock" && <StepSetup setup={s} />}
      {key === "tooling" && <StepTooling setup={s} />}
      {key === "plan" && <StepPlan setup={s} />}
      {key === "cam" && <StepCam setup={s} job={job.data ?? null} />}
      {key === "validate" && <StepProgram setup={s} job={job.data ?? null} tab="validation" />}
      {key === "export" && <StepProgram setup={s} job={job.data ?? null} tab="program" />}
    </>
  );
}
