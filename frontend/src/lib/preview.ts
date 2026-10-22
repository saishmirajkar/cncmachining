/**
 * Read-only preview mode (build with VITE_PREVIEW=1, see docs/PREVIEW.md).
 *
 * The UI is served without a backend: GET requests are answered from API responses recorded during a real run
 * of the full pipeline (e2e/workflow.spec.ts with RECORD_PREVIEW), every write is refused. Nothing in preview
 * mode computes geometry, toolpaths or programs; it only replays what the real stack produced.
 */
import { ApiError } from "./api";

export const PREVIEW = import.meta.env.VITE_PREVIEW === "1";

interface Recording {
  recorded_at: string;
  start: string;
  responses: Record<string, { contentType: string; body: unknown }>;
}

let recording: Recording | null = null;

export async function loadPreview(): Promise<void> {
  const res = await fetch("./preview-data.json");
  if (!res.ok) throw new Error(`preview data missing (${res.status})`);
  recording = (await res.json()) as Recording;
}

export function previewInfo() {
  return { recordedAt: recording?.recorded_at ?? null, start: recording?.start ?? "/" };
}

export function previewMe(): { token: string; org: string | null } {
  const me = recording?.responses["/api/auth/me"]?.body as { memberships?: { organization_id: string }[] } | undefined;
  return { token: "preview", org: me?.memberships?.[0]?.organization_id ?? null };
}

export const READ_ONLY_MESSAGE =
  "Read-only preview: this action needs the running platform (API, worker and FreeCAD). Deploy it to create or change jobs.";

/** Answer a request from the recording. Unknown query strings fall back to the same path (filters are not applied). */
export function previewResponse(path: string, method: string): unknown {
  if (method !== "GET") throw new ApiError(403, READ_ONLY_MESSAGE);
  const responses = recording?.responses ?? {};
  const hit = responses[path] ?? Object.entries(responses).find(([k]) => k.split("?")[0] === path.split("?")[0])?.[1];
  if (!hit) throw new ApiError(404, "Not part of the recorded preview.");
  return structuredClone(hit.body);
}
