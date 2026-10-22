import { ProgramWorkspace } from "@/components/program/ProgramWorkspace";
import { Alert } from "@/components/ui/misc";
import type { CamJob, Setup } from "@/lib/types";
import { StepNav } from "./common";

export function StepProgram({ setup, job, tab }: { setup: Setup; job: CamJob | null; tab: "validation" | "program" }) {
  const programId = job?.program_ids[0];
  return (
    <>
      {programId ? (
        <ProgramWorkspace key={`${programId}-${tab}`} programId={programId} defaultTab={tab} />
      ) : (
        <Alert tone="info" title="No program yet">
          Generate toolpaths first. A program is created only when every operation was computed successfully by the CAM engine.
        </Alert>
      )}
      <StepNav setup={setup} current={tab === "validation" ? "validate" : "export"} nextDisabled={!programId} />
    </>
  );
}
