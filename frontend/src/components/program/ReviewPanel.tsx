import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, ShieldAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Alert } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Revision } from "@/lib/types";
import { fmtDate, humanize } from "@/lib/utils";

const ACK_TEXT: Record<string, string> = {
  software_checks_only: "I understand that the checks are software-level only and do not prove that the machine will execute the program safely.",
  setup_verified: "Stock, datum / work offset and workholding in this program match the physical setup.",
  tool_data_verified: "Tool numbers, length offsets, holders and stick-outs match the machine's tool table.",
  first_article_prove_out: "The first run will be a controlled prove-out (single block, reduced rapid and feed overrides, dry run where applicable).",
};

export function ReviewPanel({ rev, onChanged }: { rev: Revision; onChanged: () => void }) {
  const { hasRole, me } = useAuth();
  const [acks, setAcks] = useState<string[]>([]);
  const [comments, setComments] = useState("");
  const [proof, setProof] = useState({ result: "passed", machine_serial: "", notes: "" });
  const review = useMutation({
    mutationFn: (decision: string) => api(`/api/program-revisions/${rev.id}/review`, { method: "POST", json: { decision, acknowledgements: acks, comments } }),
    onSuccess: onChanged,
  });
  const release = useMutation({ mutationFn: () => api(`/api/program-revisions/${rev.id}/release`, { method: "POST" }), onSuccess: onChanged });
  const prove = useMutation({ mutationFn: () => api(`/api/program-revisions/${rev.id}/prove-out`, { method: "POST", json: proof }), onSuccess: onChanged });
  const allPassed = rev.release_gates.every((g) => g.passed);
  const engineer = hasRole("engineer");
  const err = (review.error ?? release.error) as Error | null;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Release gates</CardTitle>
            <CardDescription>All gates must pass before the revision is marked “ready for controlled export”. Generation success alone never releases a program.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {rev.release_gates.map((g) => (
            <div key={g.code} className="flex gap-2.5 text-sm" data-testid="gate">
              {g.passed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-err" />}
              <div>
                <span className="font-mono text-xs text-muted">{g.code}</span> — {g.message}
              </div>
            </div>
          ))}
          {rev.released ? (
            <Alert tone="ok" title="Released for controlled prove-out" className="mt-3">
              Released {fmtDate(rev.released_at)}. The revision is immutable; edits create a new revision.
            </Alert>
          ) : (
            <Button className="mt-3" onClick={() => release.mutate()} disabled={!allPassed || !engineer} loading={release.isPending} data-testid="release">
              <ShieldAlert /> Release for controlled export
            </Button>
          )}
          {!engineer && <p className="text-xs text-muted">Release requires the engineer role.</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Engineering review</CardTitle>
            <CardDescription>Four-eyes rule: the author of a revision cannot review it.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {rev.review && (
            <Alert tone={rev.review.decision === "approved" ? "ok" : "err"} title={`Last review: ${rev.review.decision} (${fmtDate(rev.review.created_at)})`}>
              {rev.review.acknowledgements.map((a) => humanize(a)).join(", ") || "no acknowledgements"}
              {rev.review.comments && <div className="mt-1 italic">“{rev.review.comments}”</div>}
            </Alert>
          )}
          {!rev.released && engineer && (
            <>
              {rev.required_acknowledgements.map((a) => (
                <label key={a} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={acks.includes(a)}
                    onChange={(e) => setAcks(e.target.checked ? [...acks, a] : acks.filter((x) => x !== a))}
                    data-testid={`ack-${a}`}
                  />
                  <span>{ACK_TEXT[a] ?? humanize(a)}</span>
                </label>
              ))}
              <Field label="Comments">
                <Textarea value={comments} onChange={(e) => setComments(e.target.value)} placeholder="Required when rejecting" />
              </Field>
              <div className="flex gap-2">
                <Button onClick={() => review.mutate("approved")} loading={review.isPending} disabled={(rev.validation?.summary.errors ?? 1) > 0} data-testid="approve-review">
                  Approve
                </Button>
                <Button variant="destructive" onClick={() => review.mutate("rejected")} disabled={!comments.trim()}>
                  Reject
                </Button>
              </div>
            </>
          )}
          {!engineer && <p className="text-sm text-muted">Reviews require the engineer role ({me?.email}).</p>}
          {err && <Alert tone="err">{err.message}</Alert>}
        </CardContent>
      </Card>
      {rev.released && (
        <Card className="xl:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Physical prove-out record</CardTitle>
              <CardDescription>Recorded by the shop after the first-article run. This is the only place machine behaviour is confirmed.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {rev.prove_outs.map((p, i) => (
              <Alert key={i} tone={p.result === "passed" ? "ok" : p.result === "failed" ? "err" : "warn"} title={`${humanize(p.result)} · ${p.machine_serial || "machine not specified"} · ${fmtDate(p.created_at)}`}>
                {p.notes}
              </Alert>
            ))}
            <div className="grid grid-cols-[160px_220px_1fr_auto] items-end gap-3">
              <Field label="Result">
                <Select value={proof.result} onChange={(e) => setProof({ ...proof, result: e.target.value })}>
                  <option value="passed">Passed</option>
                  <option value="partial">Partial</option>
                  <option value="failed">Failed</option>
                </Select>
              </Field>
              <Field label="Machine serial">
                <Input value={proof.machine_serial} onChange={(e) => setProof({ ...proof, machine_serial: e.target.value })} />
              </Field>
              <Field label="Notes (inspection result, adjustments)">
                <Input value={proof.notes} onChange={(e) => setProof({ ...proof, notes: e.target.value })} />
              </Field>
              <Button variant="outline" onClick={() => prove.mutate()} disabled={proof.notes.length < 3} loading={prove.isPending}>
                Record
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
