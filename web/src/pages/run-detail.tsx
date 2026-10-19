import { useMemo, useState } from "react";
import { useParams } from "react-router";
import { PageContainer } from "@/components/layout/app-shell";
import { Section } from "@/components/common/section";
import { ChartSkeleton, DetailSkeleton, ErrorState } from "@/components/common/states";
import { CurveChart } from "@/components/run/curve-chart";
import { RunIdentityPanel } from "@/components/run/identity-panel";
import { PointStrip } from "@/components/run/point-strip";
import { PerTaskTable } from "@/components/run/per-task-table";
import { RoundDiffPanel } from "@/components/run/round-diff-panel";
import { RunHeader } from "@/components/run/run-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRun, useRuns } from "@/hooks/queries";
import { useT } from "@/i18n";
import { buildCurveView } from "@/logic/curve";
import { badgeFor } from "@/logic/certify";

/**
 * Run detail — the curve, the per-task table, and the run's own account of itself.
 *
 * The curve comes first because the platform's unit of evidence is a curve, not a final
 * score: a method that improves once and then loses it reads as a single number otherwise.
 * Score and `test_rate` are drawn as two series on two axes, and the round selector under the
 * chart drives the per-task table and the candidate diff, so "what happened in round 1" is
 * one click from the curve point that says round 1 did something.
 */
export function RunDetailPage() {
  const t = useT();
  const { runId } = useParams<{ runId: string }>();
  const bundle = useRun(runId);
  const runs = useRuns();
  const [pickedRound, setPickedRound] = useState<number | null>(null);

  const view = useMemo(
    () => (bundle.data ? buildCurveView(bundle.data.points) : null),
    [bundle.data],
  );

  // The default selection is derived rather than written by an effect: an effect would render
  // one frame with no round selected, and that frame would claim "round 0 is not in this run's
  // curve" for a run whose curve is right there.
  const selectedRound = pickedRound ?? view?.points[view.points.length - 1]?.round ?? null;
  const setSelectedRound = setPickedRound;

  const certify = useMemo(() => {
    const row = runs.data?.find((r) => r.run_id === runId);
    return row ? row.certify : null;
  }, [runs.data, runId]);

  if (bundle.isLoading) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }
  if (bundle.error) {
    return (
      <PageContainer>
        <ErrorState error={bundle.error} onRetry={() => void bundle.refetch()} />
      </PageContainer>
    );
  }
  if (!bundle.data || !view) return null;

  const point = bundle.data.points.find((p) => p.round === selectedRound) ?? null;
  const verdicts = selectedRound === null ? {} : bundle.data.verdicts[String(selectedRound)] ?? {};
  const badge = badgeFor(certify);

  return (
    <PageContainer>
      <RunHeader bundle={bundle.data} view={view} certify={certify} />

      {bundle.data.load_problems.length ? (
        <Section
          title={t("run.problems")}
          description={t("run.problemsHint")}
          className="border-warn/30"
        >
          <ul className="list-disc space-y-1 pl-4 text-2xs text-warn">
            {bundle.data.load_problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {badge.problems.length ? (
        <Section
          title={t("run.notQuotable")}
          description={t("run.notQuotableHint")}
          className="border-fail/30"
        >
          <ul className="list-disc space-y-1 pl-4 text-2xs text-fail">
            {badge.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {badge.certificationNote && badge.level === "valid" ? (
        <Section
          title={t("run.notCertified")}
          description={t("run.notCertifiedHint")}
        >
          <p className="text-2xs text-muted-foreground">{badge.certificationNote}</p>
        </Section>
      ) : null}

      <Section
        title={t("run.curve")}
        description={t("run.curveHint")}
      >
        <CurveChart
          view={view}
          selectedRound={selectedRound}
          onSelectRound={setSelectedRound}
        />
      </Section>

      <Section
        title={t("run.states")}
        description={t("run.statesHint")}
      >
        <PointStrip
          points={view.points}
          selectedRound={selectedRound}
          onSelect={setSelectedRound}
        />
      </Section>

      <Tabs defaultValue="tasks" className="space-y-1">
        <TabsList>
          <TabsTrigger value="tasks">
            {selectedRound !== null
              ? t("run.tab.tasksRound", { round: selectedRound })
              : t("run.tab.tasks")}
          </TabsTrigger>
          <TabsTrigger value="diff">
            {selectedRound !== null
              ? t("run.tab.diffRound", { round: selectedRound })
              : t("run.tab.diff")}
          </TabsTrigger>
          <TabsTrigger value="identity">{t("run.tab.identity")}</TabsTrigger>
        </TabsList>
        <TabsContent value="tasks">
          <Section
            title={t("run.tasks.title", { round: selectedRound ?? "—" })}
            description={t("run.tasks.description")}
          >
            <PerTaskTable
              runId={bundle.data.run_id}
              round={selectedRound ?? 0}
              point={point}
              verdicts={verdicts}
            />
          </Section>
        </TabsContent>
        <TabsContent value="diff">
          <Section
            title={t("run.diff.title", { round: selectedRound ?? "—" })}
            description={t("run.diff.description")}
          >
            {selectedRound === null ? (
              <ChartSkeleton />
            ) : (
              <RoundDiffPanel runId={bundle.data.run_id} round={selectedRound} />
            )}
          </Section>
        </TabsContent>
        <TabsContent value="identity">
          <Section
            title={t("run.identity.title")}
            description={t("run.identity.description")}
          >
            <RunIdentityPanel meta={bundle.data.meta} point={point} />
          </Section>
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
