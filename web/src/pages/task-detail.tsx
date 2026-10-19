import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { PageHeader, Section } from "@/components/common/section";
import { DetailSkeleton, ErrorState } from "@/components/common/states";
import { CodeView } from "@/components/common/code-view";
import { VerdictPanel } from "@/components/task/verdict-panel";
import { TraceTimeline } from "@/components/task/trace-timeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRun, useTaskPage } from "@/hooks/queries";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { formatScore } from "@/lib/format";

/**
 * Task detail — one task's verdict and its trace, next to each other.
 *
 * `docs/refactor_references.md` §6.3: "the UI should show a task's raw run next to its score,
 * because the common failure is a score that is arithmetically right and semantically wrong."
 * So the verdict (left, sticky) and the trace (right) are on screen together, and the trace is
 * a timeline rather than JSONL: the reader's question is whether the harness looped, was
 * nudged, or was clipped — all of which are events, not text.
 */
export function TaskDetailPage() {
  const t = useT();
  const { runId, round, taskId } = useParams<{ runId: string; round: string; taskId: string }>();
  const roundNumber = round !== undefined ? Number(round) : undefined;
  const page = useTaskPage(runId, roundNumber, taskId);
  const bundle = useRun(runId);

  const taskIds = useMemo(() => {
    if (!bundle.data || roundNumber === undefined) return [];
    const point = bundle.data.points.find((p) => p.round === roundNumber);
    return Object.keys(point?.per_task ?? {}).sort();
  }, [bundle.data, roundNumber]);

  if (page.isLoading) {
    return (
      <PageContainer>
        <DetailSkeleton rows={4} />
      </PageContainer>
    );
  }
  if (page.error) {
    return (
      <PageContainer>
        <ErrorState error={page.error} onRetry={() => void page.refetch()} />
      </PageContainer>
    );
  }
  if (!page.data) return null;

  const data = page.data;
  const point = bundle.data?.points.find((p) => p.round === roundNumber) ?? null;
  const score = point?.per_task?.[taskId ?? ""];
  const rate = point?.per_task_test_rate?.[taskId ?? ""];
  const diff = data.diff;

  return (
    <PageContainer>
      <PageHeader
        title={<span className="font-mono text-base">{taskId}</span>}
        description={
          <>
            {runId} · {t("task.roundN", { n: roundNumber ?? 0 })}
            {point ? (
              <>
                {" · "}
                <span className="font-mono">harness {point.identity?.harness_sha?.slice(0, 8)}</span>
              </>
            ) : null}
          </>
        }
        meta={
          <>
            <Badge variant="muted">{t("task.badgeScore", { value: formatScore(score) })}</Badge>
            <Badge variant="info">
              {t("task.badgeTestRate", {
                value:
                  rate === undefined ? t("task.absent") : rate === null ? "null" : formatScore(rate),
              })}
            </Badge>
            {data.verdict?.kind ? (
              <Badge variant="muted">{t("task.badgeVerifier", { kind: data.verdict.kind })}</Badge>
            ) : null}
          </>
        }
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link to={`/runs/${runId}`}>
              <ArrowLeft className="h-3.5 w-3.5" />
              {t("task.backToRun")}
            </Link>
          </Button>
        }
      />

      {taskIds.length ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-2xs text-muted-foreground">
            {t("task.tasksInRound", { n: roundNumber ?? 0 })}
          </span>
          {taskIds.map((id) => (
            <Link
              key={id}
              to={`/runs/${runId}/tasks/${roundNumber}/${id}`}
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-2xs transition-colors",
                id === taskId
                  ? "border-ring bg-accent text-accent-foreground"
                  : "border-border text-muted-foreground hover:bg-accent/50",
              )}
            >
              {id}
            </Link>
          ))}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <div className="xl:sticky xl:top-14 xl:self-start">
          <Section
            title={t("task.verdictSection")}
            description={t("task.verdictSectionHint")}
          >
            <VerdictPanel verdict={data.verdict} problems={data.verdict_problems} />
          </Section>
        </div>

        <Tabs defaultValue="trace">
          <TabsList>
            <TabsTrigger value="trace">{t("task.tabTrace")}</TabsTrigger>
            <TabsTrigger value="diff">{t("task.tabDiff")}</TabsTrigger>
            <TabsTrigger value="raw">{t("task.tabRaw")}</TabsTrigger>
          </TabsList>
          <TabsContent value="trace">
            <Section
              title={t("task.traceSection")}
              description={t("task.traceSectionHint")}
            >
              {data.trace_problems.length ? (
                <div className="mb-2 space-y-1">
                  {data.trace_problems.map((p) => (
                    <p key={p} className="text-2xs text-warn">
                      {p}
                    </p>
                  ))}
                </div>
              ) : null}
              <TraceTimeline events={data.trace} />
            </Section>
          </TabsContent>
          <TabsContent value="diff">
            <Section
              title={t("task.diffSection", { n: roundNumber ?? 0 })}
              description={t("task.diffSectionHint")}
            >
              <CodeView
                value={diff?.patch ?? ""}
                language="diff"
                height={480}
                emptyLabel={t("task.noPatchThisRound")}
              />
            </Section>
          </TabsContent>
          <TabsContent value="raw">
            <Section
              title={t("task.rawSection")}
              description={t("task.rawSectionHint")}
            >
              <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/30 px-2.5 py-2 font-mono text-2xs">
                {data.trace.map((event) => JSON.stringify(event)).join("\n") || t("task.noEvents")}
              </pre>
            </Section>
          </TabsContent>
        </Tabs>
      </div>
    </PageContainer>
  );
}
