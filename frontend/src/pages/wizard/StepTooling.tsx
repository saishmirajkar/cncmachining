import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { ToolForm } from "@/components/ToolForm";
import { ToolTable } from "@/components/ToolTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Select } from "@/components/ui/form";
import { Alert, Dialog, DialogContent } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { Setup, ToolLibrary } from "@/lib/types";
import { RequirementList, StepNav, usePatchSetup } from "./common";

export function StepTooling({ setup }: { setup: Setup }) {
  const qc = useQueryClient();
  const libs = useQuery({ queryKey: ["tool-libraries"], queryFn: () => api<ToolLibrary[]>("/api/tool-libraries") });
  const patch = usePatchSetup(setup.id);
  const [adding, setAdding] = useState(false);
  const example = useMutation({
    mutationFn: () => api<ToolLibrary>("/api/tool-libraries/example", { method: "POST" }),
    onSuccess: (l) => {
      qc.invalidateQueries({ queryKey: ["tool-libraries"] });
      patch.mutate({ tool_library_id: l.id });
    },
  });
  const lib = libs.data?.find((l) => l.id === setup.tool_library_id);
  return (
    <>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Tool library for this setup</CardTitle>
            <CardDescription>The planner only uses tools in this library. Tool numbers must match the machine's magazine and offset tables.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => example.mutate()} loading={example.isPending}>
              Create example library
            </Button>
            {lib && (
              <Button onClick={() => setAdding(true)}>
                <Plus /> Add tool
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Library" className="max-w-md">
            <Select value={setup.tool_library_id ?? ""} onChange={(e) => e.target.value && patch.mutate({ tool_library_id: e.target.value })} data-testid="tool-library">
              <option value="">select…</option>
              {libs.data?.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.tools.length} tools)
                </option>
              ))}
            </Select>
          </Field>
          {lib?.name.toLowerCase().includes("example") && (
            <Alert tone="warn" title="Example tool data">
              Dimensions, offsets and holder data in the example library are illustrative. Measure and correct every tool before
              generating a production program.
            </Alert>
          )}
          <RequirementList items={setup.requirements} fields={["tool_library"]} />
        </CardContent>
        {lib && <ToolTable library={lib} editable limits={setup.machine_configuration?.effective_limits} />}
      </Card>
      <Dialog open={adding} onOpenChange={setAdding}>
        {lib && (
          <DialogContent title="Add tool" className="w-[min(820px,calc(100vw-2rem))]">
            <ToolForm libraryId={lib.id} onDone={() => setAdding(false)} />
          </DialogContent>
        )}
      </Dialog>
      <StepNav setup={setup} current="tooling" nextDisabled={!setup.tool_library_id} />
    </>
  );
}
