import { Boxes } from "lucide-react";
import { useState } from "react";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { useAuth } from "@/lib/auth";

export function AuthPage() {
  const { me, login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", full_name: "", organization_name: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (me) return <Navigate to="/" replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(form.email, form.password);
      else await register(form);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="grid min-h-full lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden flex-col justify-between bg-sidebar p-12 text-sidebar-fg lg:flex">
        <div className="flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-md bg-white/10">
            <Boxes className="size-5 text-sky-300" />
          </div>
          <span className="text-base font-semibold text-white">CNC Programming Platform</span>
        </div>
        <div className="max-w-md space-y-5">
          <h2 className="text-3xl font-semibold leading-tight text-white">From engineering model to a reviewed, traceable CNC program.</h2>
          <ul className="space-y-2.5 text-sm text-sidebar-fg/85">
            <li>Deterministic geometry analysis with Open CASCADE</li>
            <li>Toolpaths computed by the FreeCAD CAM engine</li>
            <li>Controller-specific, versioned postprocessors</li>
            <li>Layered validation, Z-map simulation and engineering release</li>
          </ul>
        </div>
        <p className="text-xs text-sidebar-fg/50">
          Programs are never transmitted to machines. Software checks do not replace a physical prove-out.
        </p>
      </div>
      <div className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div>
            <h1 className="text-xl font-semibold">{mode === "login" ? "Sign in" : "Create your organisation"}</h1>
            <p className="mt-1 text-sm text-muted">
              {mode === "login" ? "Use your work account." : "You become the owner of a new organisation workspace."}
            </p>
          </div>
          {mode === "register" && (
            <>
              <Field label="Full name">
                <Input value={form.full_name} onChange={set("full_name")} autoComplete="name" />
              </Field>
              <Field label="Organisation name" required>
                <Input value={form.organization_name} onChange={set("organization_name")} required minLength={2} />
              </Field>
            </>
          )}
          <Field label="Email" required>
            <Input type="email" value={form.email} onChange={set("email")} required autoComplete="email" />
          </Field>
          <Field label="Password" required hint={mode === "register" ? "At least 10 characters" : undefined}>
            <Input type="password" value={form.password} onChange={set("password")} required minLength={mode === "register" ? 10 : 1} autoComplete={mode === "login" ? "current-password" : "new-password"} />
          </Field>
          {error && <div className="rounded-md border border-err/25 bg-err-soft px-3 py-2 text-sm text-err">{error}</div>}
          <Button type="submit" className="w-full" loading={busy}>
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
          <p className="text-center text-sm text-muted">
            {mode === "login" ? "No account yet? " : "Already registered? "}
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Create an organisation" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
