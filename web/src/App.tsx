import { Link, Navigate, Route, Routes } from "react-router";
import { AppShell, PageContainer } from "@/components/layout/app-shell";
import { EmptyState } from "@/components/common/states";
import { ExperimentsPage } from "@/pages/experiments";
import { RunDetailPage } from "@/pages/run-detail";
import { ComparePage } from "@/pages/compare";
import { TaskDetailPage } from "@/pages/task-detail";
import { LaunchPage } from "@/pages/launch";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

/**
 * The five screens. `/compare` is a top-level destination rather than a mode of the run
 * detail because it is the question the platform exists to answer, and burying it one click
 * deeper would make the number the default view again.
 */
export function App() {
  const t = useT();
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/experiments" replace />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="runs/:runId/tasks/:round/:taskId" element={<TaskDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="launch" element={<LaunchPage />} />
        <Route
          path="*"
          element={
            <PageContainer>
              <EmptyState
                title={t("app.noSuchScreen")}
                description={t("app.noSuchScreenHint")}
                action={
                  <Button asChild size="sm" variant="outline">
                    <Link to="/experiments">{t("app.backToExperiments")}</Link>
                  </Button>
                }
              />
            </PageContainer>
          }
        />
      </Route>
    </Routes>
  );
}
