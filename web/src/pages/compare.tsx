import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowLeftRight, Info } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { PageHeader, Section } from "@/components/common/section";
import { ChartSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { CandidateDiffs } from "@/components/compare/candidate-diffs";
import { FieldDiffTable } from "@/components/compare/field-diff-table";
import { FlipSummary } from "@/components/compare/flip-summary";
import { RunPicker } from "@/components/compare/run-picker";
import { TaskDeltaTable } from "@/components/compare/task-delta-table";
import { CurveChart } from "@/components/run/curve-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useRun, useRuns } from "@/hooks/queries";
import { useT } from "@/i18n";
import { compareRuns } from "@/logic/compare";
import { buildCurveView } from "@/logic/curve";
import type { RunBundle } from "@/types";

/**
 * Compare — two runs, two rounds, side by side. The most important screen.
 *
 * It is laid out so the answer to "did this edit help?" is at the top and the evidence for it
 * is underneath, in the order a reader checks it:
 *
 * 1. the headline: score and `test_rate` deltas, and the gained / lost / still-passing counts;
 * 2. the movement strip: which tasks moved, in task order;
 * 3. the two curves side by side (small multiples — never one shared axis, which would imply
 *    the two runs were measured on the same absolute scale);
 * 4. the per-task delta table, with `test_rate` change and each side's verdict detail;
 * 5. the two records diffed field by field;
 * 6. the two candidate diffs.
 *
 * The selection lives in the URL, so a comparison can be linked to in a report.
 */
export function ComparePage() {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const runs = useRuns();

  const available = runs.data ?? [];
  const leftId = params.get("left") ?? available[0]?.run_id ?? "";
  const rightId = params.get("right") ?? available[1]?.run_id ?? leftId;
  const leftRoundParam = params.get("leftRound");
  const rightRoundParam = params.get("rightRound");

  const left = useRun(leftId || undefined);
  const right = useRun(rightId || undefined);
  const [onlyMoved, setOnlyMoved] = useState(true);

  /**
   * All param writes go through the functional updater. Two `useEffect`s default each side's
   * round, and rebuilding the query string from the render-time `params` in both would make
   * the second call drop the first one's key.
   */
  const mutateParams = (mutate: (next: URLSearchParams) => void) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        mutate(next);
        return next;
      },
      { replace: true },
    );
  };

  const selectRun = (side: "left" | "right", runId: string) =>
    mutateParams((next) => {
      next.set(side, runId);
      // A different run means a different set of rounds; fall back to its last one.
      next.delete(`${side}Round`);
    });

  const swap = () =>
    mutateParams((next) => {
      const l = next.get("left") ?? "";
      const r = next.get("right") ?? "";
      const lr = next.get("leftRound");
      const rr = next.get("rightRound");
      next.set("left", r);
      next.set("right", l);
      if (lr !== null) next.set("leftRound", rr ?? "");
      if (rr !== null) next.set("rightRound", lr ?? "");
      if (lr === null) next.delete("leftRound");
      if (rr === null) next.delete("rightRound");
    });

  const lastRoundOf = (bundle: RunBundle | undefined): number | null => {
    const points = bundle?.points;
    if (!points?.length) return null;
    return Math.max(...points.map((p) => p.round));
  };

  // Default each side to its own last measured round, once both bundles are loaded.
  useEffect(() => {
    const leftLast = lastRoundOf(left.data);
    const rightLast = lastRoundOf(right.data);
    if (leftLast === null && rightLast === null) return;
    mutateParams((next) => {
      if (leftLast !== null && !next.get("leftRound")) next.set("leftRound", String(leftLast));
      if (rightLast !== null && !next.get("rightRound")) next.set("rightRound", String(rightLast));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left.data, right.data]);

  const comparison = useMemo(() => {
    if (!left.data || !right.data) return null;
    return compareRuns(left.data, right.data, {
      leftRound: leftRoundParam !== null ? Number(leftRoundParam) : undefined,
      rightRound: rightRoundParam !== null ? Number(rightRoundParam) : undefined,
    });
  }, [left.data, right.data, leftRoundParam, rightRoundParam]);

  if (runs.isLoading || left.isLoading || right.isLoading) {
    return (
      <PageContainer>
        <PageHeader title={t("compare.title")} description={t("compare.loading")} />
        <ChartSkeleton />
        <ChartSkeleton />
      </PageContainer>
    );
  }
  if (runs.error) {
    return (
      <PageContainer>
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      </PageContainer>
    );
  }
  if (!available.length) {
    return (
      <PageContainer>
        <EmptyState
          title={t("compare.noRuns")}
          description={t("compare.noRunsHint")}
        />
      </PageContainer>
    );
  }
  if (left.error || right.error) {
    return (
      <PageContainer>
        <PageHeader title={t("compare.title")} />
        {left.error ? <ErrorState error={left.error} onRetry={() => void left.refetch()} /> : null}
        {right.error ? <ErrorState error={right.error} onRetry={() => void right.refetch()} /> : null}
      </PageContainer>
    );
  }
  if (!comparison || !left.data || !right.data) return null;

  const leftView = buildCurveView(left.data.points);
  const rightView = buildCurveView(right.data.points);

  const roundsFor = (runId: string): number[] => {
    if (runId === leftId && left.data) return left.data.points.map((p) => p.round).sort((a, b) => a - b);
    if (runId === rightId && right.data) return right.data.points.map((p) => p.round).sort((a, b) => a - b);
    return available.find((r) => r.run_id === runId)?.certify.rounds ?? [];
  };

  return (
    <PageContainer>
      <PageHeader
        title={t("compare.title")}
        description={t("compare.description")}
        actions={
          <Button variant="outline" size="sm" onClick={swap}>
            <ArrowLeftRight className="h-3.5 w-3.5" />
            {t("compare.swap")}
          </Button>
        }
      />

      <Section title={t("compare.selection")} description={t("compare.selectionHint")}>
        <RunPicker
          runs={available}
          leftId={leftId}
          rightId={rightId}
          leftRound={comparison.leftRound}
          rightRound={comparison.rightRound}
          onLeft={(v) => selectRun("left", v)}
          onRight={(v) => selectRun("right", v)}
          onLeftRound={(v) => mutateParams((next) => next.set("leftRound", String(v)))}
          onRightRound={(v) => mutateParams((next) => next.set("rightRound", String(v)))}
          onSwap={swap}
          roundsFor={roundsFor}
        />
      </Section>

      <Section
        title={t("compare.whatMoved")}
        description={t("compare.whatMovedHint")}
      >
        {comparison.invalidReason ? (
          <Alert variant="warn"><AlertDescription>{comparison.summary.headline}</AlertDescription></Alert>
        ) : null}
        <FlipSummary comparison={comparison} />
      </Section>

      {comparison.leftRound !== comparison.rightRound ? (
        <Alert variant="warn">
          <Info className="h-4 w-4" />
          <AlertDescription>
            {t("compare.differentRounds", {
              left: comparison.leftRound ?? "",
              right: comparison.rightRound ?? "",
            })}
          </AlertDescription>
        </Alert>
      ) : null}

      <Section
        title={t("compare.curves")}
        description={t("compare.curvesHint")}
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Link
                to={`/runs/${left.data.run_id}`}
                className="font-mono text-xs font-medium underline-offset-2 hover:underline"
              >
                {left.data.run_id}
              </Link>
              <Badge variant="muted">
                {t("compare.scoreRateBadge", {
                  score: comparison.left?.score?.toFixed(3) ?? "—",
                  rate: comparison.left?.testRate?.toFixed(3) ?? "—",
                })}
              </Badge>
            </div>
            <CurveChart view={leftView} height={220} selectedRound={comparison.leftRound} />
          </div>
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Link
                to={`/runs/${right.data.run_id}`}
                className="font-mono text-xs font-medium underline-offset-2 hover:underline"
              >
                {right.data.run_id}
              </Link>
              <Badge variant="muted">
                {t("compare.scoreRateBadge", {
                  score: comparison.right?.score?.toFixed(3) ?? "—",
                  rate: comparison.right?.testRate?.toFixed(3) ?? "—",
                })}
              </Badge>
            </div>
            <CurveChart view={rightView} height={220} selectedRound={comparison.rightRound} />
          </div>
        </div>
      </Section>

      <Section
        title={t("compare.perTaskDelta")}
        description={t("compare.perTaskDeltaHint")}
        actions={
          <div className="flex items-center gap-2">
            <Switch id="only-moved" checked={onlyMoved} onCheckedChange={setOnlyMoved} />
            <Label htmlFor="only-moved">{t("compare.onlyMoved")}</Label>
          </div>
        }
      >
        <TaskDeltaTable comparison={comparison} onlyMoved={onlyMoved} />
      </Section>

      <Section
        title={t("compare.recordDiff")}
        description={t("compare.recordDiffHint")}
      >
        <FieldDiffTable
          fields={comparison.fields}
          leftLabel={t("compare.sideLabel", {
            run: comparison.leftRunId,
            round: String(comparison.leftRound),
          })}
          rightLabel={t("compare.sideLabel", {
            run: comparison.rightRunId,
            round: String(comparison.rightRound),
          })}
        />
      </Section>

      <Section
        title={t("compare.candidateDiffs")}
        description={t("compare.candidateDiffsHint")}
      >
        <CandidateDiffs comparison={comparison} leftBundle={left.data} rightBundle={right.data} />
      </Section>
    </PageContainer>
  );
}
