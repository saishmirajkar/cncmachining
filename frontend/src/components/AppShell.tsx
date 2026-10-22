import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  ClipboardCheck,
  Cpu,
  FileCode2,
  FolderKanban,
  History,
  LayoutDashboard,
  LogOut,
  PlusSquare,
  Settings,
  Wrench,
} from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { Select } from "@/components/ui/form";
import { Tip } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PREVIEW, previewInfo } from "@/lib/preview";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/jobs/new", label: "New CNC Job", icon: PlusSquare },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/machines", label: "Machine Library", icon: Cpu },
  { to: "/tools", label: "Tool Library", icon: Wrench },
  { to: "/postprocessors", label: "Postprocessors", icon: FileCode2 },
  { to: "/history", label: "Job History", icon: History },
  { to: "/reports", label: "Validation Reports", icon: ClipboardCheck },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface Health {
  status: string;
  queue_backend: string;
  cam_engines: { name: string; available: boolean; detail: string; supported_operations: string[] }[];
}

function PreviewBanner() {
  const { recordedAt, start } = previewInfo();
  return (
    <div className="border-b border-warn/30 bg-warn-soft px-4 py-2.5 text-[13px] leading-snug text-foreground sm:px-8" role="note">
      <strong>Read-only preview.</strong> Every screen shows data recorded
      {recordedAt && ` on ${new Date(recordedAt).toLocaleDateString(undefined, { dateStyle: "medium" })}`} from one real run of
      the platform: STEP upload, geometry analysis, FreeCAD CAM, LinuxCNC postprocessor, validation, simulation and a
      two-person release. Creating or changing anything is disabled; deploy the platform to run your own jobs. Checks are
      software-level only — no program here has been proven on a machine.{" "}
      <Link to={start} className="font-medium text-accent underline underline-offset-2">
        Open the recorded job
      </Link>
    </div>
  );
}

export function AppShell() {
  const { me, membership, logout, switchOrg } = useAuth();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health"), staleTime: 60_000 });
  const cam = health.data?.cam_engines[0];

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col bg-sidebar text-sidebar-fg lg:flex">
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-6">
          <div className="grid size-8 place-items-center rounded-md bg-white/10">
            <Boxes className="size-4.5 text-sky-300" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-white">CNC Programming</div>
            <div className="text-[11px] text-sidebar-fg/60">CAD · CAM · Post · Validate</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors hover:bg-white/5 hover:text-white",
                  isActive && "bg-sidebar-active text-white",
                )
              }
            >
              <Icon className="size-4 opacity-80" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-3 border-t border-white/10 px-4 py-4 text-xs">
          <Tip content={cam ? cam.detail : "Checking CAM runtime…"}>
            <div className="flex items-center gap-2">
              <span className={cn("size-2 rounded-full", cam?.available ? "bg-emerald-400" : cam ? "bg-red-400" : "bg-slate-500")} />
              <span className="text-sidebar-fg/80">
                CAM engine {cam ? (cam.available ? "available" : "unavailable") : "…"}{PREVIEW && cam && " (when recorded)"}
              </span>
            </div>
          </Tip>
          {me && me.memberships.length > 1 && (
            <Select
              value={membership?.organization_id}
              onChange={(e) => switchOrg(e.target.value)}
              className="h-8 border-white/15 bg-white/5 text-xs text-white"
              aria-label="Organization"
            >
              {me.memberships.map((m) => (
                <option key={m.organization_id} value={m.organization_id} className="text-black">
                  {m.organization_name}
                </option>
              ))}
            </Select>
          )}
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate font-medium text-white">{me?.full_name || me?.email}</div>
              <div className="truncate text-sidebar-fg/60">
                {membership?.organization_name} · {membership?.role}
                {me?.is_platform_admin && " · platform admin"}
              </div>
            </div>
            {!PREVIEW && (
              <button onClick={logout} className="rounded p-1.5 hover:bg-white/10" aria-label="Sign out" title="Sign out">
                <LogOut className="size-4" />
              </button>
            )}
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <nav className="flex gap-1 overflow-x-auto bg-sidebar px-3 py-2 lg:hidden" aria-label="Main">
          {NAV.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn("shrink-0 rounded-md px-2.5 py-1.5 text-xs font-medium text-sidebar-fg", isActive && "bg-sidebar-active text-white")
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        {PREVIEW && <PreviewBanner />}
        <div className="mx-auto max-w-[1440px] px-4 py-7 sm:px-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
