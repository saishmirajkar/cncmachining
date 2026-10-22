import { AlertTriangle, CheckCircle2, CircleDashed, Info, ShieldCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Severity } from "@/lib/types";
import { humanize } from "@/lib/utils";

type Tone = "neutral" | "accent" | "ok" | "warn" | "err" | "info" | "solid";

const JOB: Record<string, [string, Tone]> = {
  uploaded: ["Uploaded", "neutral"],
  geometry_analyzed: ["Geometry analyzed", "info"],
  setup_incomplete: ["Setup incomplete", "warn"],
  ready_for_cam: ["Ready for CAM", "accent"],
  cam_processing: ["CAM processing", "accent"],
  toolpaths_generated: ["Toolpaths generated", "info"],
  postprocessing: ["Postprocessing", "accent"],
  validation_required: ["Validation required", "warn"],
  validation_failed: ["Validation failed", "err"],
  engineering_review_required: ["Engineering review required", "warn"],
  ready_for_controlled_export: ["Ready for controlled export", "ok"],
  cam_failed: ["CAM failed", "err"],
  cancelled: ["Cancelled", "neutral"],
  analyzing: ["Analyzing", "accent"],
  analyzed: ["Analyzed", "ok"],
  failed: ["Failed", "err"],
  pending: ["Pending", "neutral"],
  running: ["Running", "accent"],
  completed: ["Completed", "ok"],
};

export function JobStatusBadge({ status }: { status: string }) {
  const [label, tone] = JOB[status] ?? [humanize(status), "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

const VERIFY: Record<string, [string, Tone, string]> = {
  draft: ["Draft", "neutral", "Entered but not reviewed"],
  documented: ["Documented", "info", "Supported by identified technical documentation"],
  tested: ["Tested (software)", "accent", "Output tested against expected behaviour at software level"],
  machine_verified: ["Machine verified", "ok", "Reviewed against the exact machine/controller configuration"],
  deprecated: ["Deprecated", "err", "No longer recommended for new jobs"],
};

export function VerificationBadge({ status }: { status: string }) {
  const [label, tone, title] = VERIFY[status] ?? [humanize(status), "neutral", ""];
  return (
    <Badge tone={tone} title={title}>
      {status === "machine_verified" && <ShieldCheck className="size-3" />}
      {label}
    </Badge>
  );
}

export function SeverityIcon({ severity, className = "size-4" }: { severity: Severity | string; className?: string }) {
  if (severity === "error") return <XCircle className={`${className} text-err`} />;
  if (severity === "warning") return <AlertTriangle className={`${className} text-warn`} />;
  if (severity === "info") return <Info className={`${className} text-info`} />;
  return <CircleDashed className={`${className} text-muted`} />;
}

export function ValidationBadge({ status }: { status: string }) {
  if (status === "passed")
    return (
      <Badge tone="ok">
        <CheckCircle2 className="size-3" /> Passed
      </Badge>
    );
  if (status === "passed_with_warnings")
    return (
      <Badge tone="warn">
        <AlertTriangle className="size-3" /> Passed with warnings
      </Badge>
    );
  return (
    <Badge tone="err">
      <XCircle className="size-3" /> Failed
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const tone: Tone = severity === "error" ? "err" : severity === "warning" ? "warn" : "info";
  return <Badge tone={tone}>{severity}</Badge>;
}
