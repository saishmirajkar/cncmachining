import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/form";
import { Alert, KV, PageHeader, Table, Td, Th } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/utils";

export function Settings() {
  const { me, membership, hasRole } = useAuth();
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<{ user_id: string; email: string; full_name: string; role: string }[]>("/api/organization/members") });
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<{ id: string; action: string; entity_type: string; entity_id: string; created_at: string }[]>("/api/audit?limit=50"),
    enabled: hasRole("engineer"),
  });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<any>("/api/health") });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("programmer");
  const add = useMutation({
    mutationFn: () => api("/api/organization/members", { method: "POST", json: { email, role } }),
    onSuccess: () => {
      setEmail("");
      qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
  return (
    <>
      <PageHeader title="Settings" description="Organisation members, roles, runtime status and audit trail." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{membership?.organization_name}</CardTitle>
              <CardDescription>
                Signed in as {me?.email} ({membership?.role})
              </CardDescription>
            </div>
          </CardHeader>
          <Table>
            <thead>
              <tr>
                <Th>Member</Th>
                <Th>Role</Th>
              </tr>
            </thead>
            <tbody>
              {members.data?.map((m) => (
                <tr key={m.user_id}>
                  <Td>
                    {m.full_name || m.email}
                    <div className="text-xs text-muted">{m.email}</div>
                  </Td>
                  <Td className="capitalize">{m.role}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          {hasRole("admin") && (
            <CardContent className="flex items-end gap-2 pt-4">
              <Field label="Add registered user by email" className="flex-1">
                <Input value={email} onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Select value={role} onChange={(e) => setRole(e.target.value)} className="w-36">
                {["viewer", "operator", "programmer", "engineer", "admin"].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </Select>
              <Button onClick={() => add.mutate()} disabled={!email} loading={add.isPending}>
                Add
              </Button>
            </CardContent>
          )}
          {add.error && (
            <CardContent>
              <Alert tone="err">{(add.error as Error).message}</Alert>
            </CardContent>
          )}
          <CardContent className="text-xs text-muted">
            Roles: viewer (read) · operator (record prove-outs) · programmer (designs, setups, plans, CAM) · engineer (reviews, release, organisation
            machines) · admin (members).
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Runtime</CardTitle>
            </CardHeader>
            <CardContent>
              <KV
                cols={1}
                items={[
                  ["API", health.data?.status],
                  ["Environment", health.data?.env],
                  ["Job queue", health.data?.queue_backend],
                  ...((health.data?.cam_engines ?? []).map((e: any) => [`CAM engine ${e.name}`, `${e.available ? "available" : "UNAVAILABLE"} — ${e.detail}`]) as [string, string][]),
                ]}
              />
            </CardContent>
          </Card>
          {hasRole("engineer") && (
            <Card>
              <CardHeader>
                <CardTitle>Recent audit events</CardTitle>
              </CardHeader>
              <div className="max-h-96 overflow-y-auto">
                <Table>
                  <tbody>
                    {audit.data?.map((e) => (
                      <tr key={e.id}>
                        <Td className="font-mono text-xs">{e.action}</Td>
                        <Td className="text-xs text-muted">
                          {e.entity_type} {e.entity_id.slice(0, 8)}
                        </Td>
                        <Td className="text-xs text-muted">{fmtDate(e.created_at)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
