import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Spinner, TooltipProvider } from "@/components/ui/misc";
import { AuthProvider, useAuth } from "@/lib/auth";
import { PREVIEW } from "@/lib/preview";
import { AuthPage } from "@/pages/AuthPage";
import { Dashboard } from "@/pages/Dashboard";
import { History, JobDetail, ProgramPage, Reports, RevisionPage } from "@/pages/History";
import { PostprocessorDetail, Postprocessors, Tools } from "@/pages/Libraries";
import { Machines } from "@/pages/Machines";
import { ProjectDetail, Projects } from "@/pages/Projects";
import { Settings } from "@/pages/Settings";
import { WizardDesign, WizardNew, WizardSetup } from "@/pages/wizard/Wizard";

// The preview is served from a static location without server-side routing, so it uses hash URLs.
const Router = PREVIEW ? HashRouter : BrowserRouter;

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function Protected() {
  const { me, loading } = useAuth();
  if (loading)
    return (
      <div className="grid h-full place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  if (!me) return <Navigate to="/login" replace />;
  return <AppShell />;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <TooltipProvider>
          <Router>
            <Routes>
              <Route path="/login" element={<AuthPage />} />
              <Route element={<Protected />}>
                <Route index element={<Dashboard />} />
                <Route path="jobs/new" element={<WizardNew />} />
                <Route path="jobs/new/design/:designId" element={<WizardDesign />} />
                <Route path="jobs/:setupId/:step" element={<WizardSetup />} />
                <Route path="projects" element={<Projects />} />
                <Route path="projects/:projectId" element={<ProjectDetail />} />
                <Route path="machines" element={<Machines />} />
                <Route path="tools" element={<Tools />} />
                <Route path="postprocessors" element={<Postprocessors />} />
                <Route path="postprocessors/:postId" element={<PostprocessorDetail />} />
                <Route path="history" element={<History />} />
                <Route path="history/:jobId" element={<JobDetail />} />
                <Route path="reports" element={<Reports />} />
                <Route path="programs/:programId" element={<ProgramPage />} />
                <Route path="revisions/:revisionId" element={<RevisionPage />} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </Router>
        </TooltipProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
