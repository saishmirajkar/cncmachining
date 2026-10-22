import { FileUp, X } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { ApiError, uploadWithProgress } from "@/lib/api";
import type { Design } from "@/lib/types";
import { cn, fmtBytes } from "@/lib/utils";

const ACCEPT = [".step", ".stp", ".iges", ".igs", ".stl", ".dxf", ".fcstd"];
export const MAX_UPLOAD_MB = 100;

export function FileDropzone({ projectId, onUploaded }: { projectId: string; onUploaded: (d: Design) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "analyzing">("idle");
  const cancelRef = useRef<() => void>(() => {});

  function start(f: File) {
    setError(null);
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPT.includes(ext)) {
      setError(`Unsupported file type '${ext}'. Supported: ${ACCEPT.join(", ")}`);
      return;
    }
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`File is ${fmtBytes(f.size)}; the limit is ${MAX_UPLOAD_MB} MB`);
      return;
    }
    if (f.size === 0) {
      setError("The file is empty");
      return;
    }
    setFile(f);
    setPhase("uploading");
    setProgress(0);
    const form = new FormData();
    form.append("file", f);
    const { promise, cancel } = uploadWithProgress(`/api/projects/${projectId}/designs`, form, (p) => {
      setProgress(p);
      if (p >= 1) setPhase("analyzing");
    });
    cancelRef.current = cancel;
    promise
      .then((d) => {
        setPhase("idle");
        setFile(null);
        onUploaded(d as Design);
      })
      .catch((e: ApiError) => {
        setPhase("idle");
        setError(e.message);
      });
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files[0];
          if (f && phase === "idle") start(f);
        }}
        className={cn(
          "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
          drag ? "border-accent bg-accent-soft" : "border-border-strong bg-surface",
        )}
      >
        <div className="grid size-11 place-items-center rounded-full bg-surface-2">
          <FileUp className="size-5 text-muted" />
        </div>
        <div>
          <div className="text-sm font-medium">Drop a CAD file here or browse</div>
          <div className="mt-1 text-xs text-muted">
            STEP (.step/.stp), IGES (.iges/.igs), STL, DXF, FreeCAD (.FCStd) · up to {MAX_UPLOAD_MB} MB · contents are
            verified, not just the extension
          </div>
        </div>
        <input
          ref={input}
          type="file"
          className="hidden"
          accept={ACCEPT.join(",")}
          data-testid="file-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) start(f);
            e.target.value = "";
          }}
        />
        <Button variant="outline" onClick={() => input.current?.click()} disabled={phase !== "idle"}>
          Choose file
        </Button>
      </div>
      {file && phase !== "idle" && (
        <div className="rounded-md border border-border bg-surface p-3">
          <div className="mb-2 flex items-center justify-between gap-3 text-sm">
            <div className="min-w-0 truncate">
              <span className="font-medium">{file.name}</span> <span className="text-muted">· {fmtBytes(file.size)}</span>
            </div>
            {phase === "uploading" ? (
              <Button size="sm" variant="ghost" onClick={() => cancelRef.current()}>
                <X /> Cancel
              </Button>
            ) : (
              <span className="text-xs text-muted">Parsing and analysing geometry…</span>
            )}
          </div>
          <Progress value={phase === "uploading" ? progress : 1} />
        </div>
      )}
      {error && <div className="rounded-md border border-err/25 bg-err-soft px-3 py-2 text-sm text-err">{error}</div>}
    </div>
  );
}
