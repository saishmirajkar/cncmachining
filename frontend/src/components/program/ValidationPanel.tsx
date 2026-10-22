import { RefreshCw } from "lucide-react";
import { SeverityIcon, ValidationBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/misc";
import type { Revision } from "@/lib/types";
import { fmtDate, humanize } from "@/lib/utils";

const LAYERS = ["geometry", "toolpath", "postprocessor", "simulation", "machine"];

export function ValidationPanel({ rev, onLine, onRevalidate, revalidating }: { rev: Revision; onLine: (l: number) => void; onRevalidate?: () => void; revalidating?: boolean }) {
  const v = rev.validation;
  if (!v) return <EmptyState title="Not validated">This revision has no validation report yet.</EmptyState>;
  return (
    <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              Validation report <ValidationBadge status={v.status} />
            </CardTitle>
            <CardDescription>
              {v.summary.errors} error(s) · {v.summary.warnings} warning(s) · {fmtDate(v.created_at)} · {Object.values(v.engine_versions).join(" · ")}
            </CardDescription>
          </div>
          {onRevalidate && !rev.released && (
            <Button size="sm" variant="outline" onClick={onRevalidate} loading={revalidating}>
              <RefreshCw /> Re-validate
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          {LAYERS.map((layer) => {
            const items = v.checks.filter((c) => c.layer === layer);
            return (
              <div key={layer}>
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
                  {humanize(layer)} checks
                  <Badge tone={items.some((i) => i.severity === "error") ? "err" : items.length ? "warn" : "ok"}>
                    {items.some((i) => i.severity === "error") ? "failed" : items.length ? `${items.length} finding(s)` : "no findings"}
                  </Badge>
                </div>
                <div className="space-y-1.5">
                  {items.map((c, i) => (
                    <div key={i} className="flex gap-2.5 rounded-md border border-border px-3 py-2 text-sm" data-testid="finding">
                      <SeverityIcon severity={c.severity} className="mt-0.5 size-4 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div>{c.message}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
                          <span className="font-mono">{c.code}</span>
                          {c.lines && c.lines.length > 0 && (
                            <span>
                              lines{" "}
                              {c.lines.slice(0, 8).map((l) => (
                                <button key={l} className="mr-1 font-mono text-accent hover:underline" onClick={() => onLine(l)}>
                                  {l}
                                </button>
                              ))}
                              {c.lines.length > 8 && `+${c.lines.length - 8}`}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>
      <Card className="h-fit">
        <CardHeader>
          <div>
            <CardTitle>Verification levels</CardTitle>
            <CardDescription>Each level is reported separately. A passing software check is not a machine verification.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="space-y-2.5 text-sm">
            {Object.entries(v.verification_levels).map(([k, val]) => (
              <div key={k} className="flex items-start justify-between gap-4 border-b border-border pb-2 last:border-0">
                <dt className="text-muted">{humanize(k)}</dt>
                <dd className="text-right font-medium">{val}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
