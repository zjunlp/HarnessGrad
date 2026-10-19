import { Link } from "react-router";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DeltaPill } from "@/components/common/stat";
import { EmptyState } from "@/components/common/states";
import { Value } from "@/components/common/presence-value";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/en";
import { firstLine, formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { RunComparison, TaskMove } from "@/logic/compare";

/**
 * The per-task delta: the table the headline strip summarises.
 *
 * `test_rate` gets its own delta column because it is the only signal that resolves a task
 * whose binary reward did not move while its check tally did. Each side's verdict `detail`
 * is one truncated line (full text on hover) so a reader can see *why* a task flipped without
 * leaving the screen — the difference between "reward 0" and "the layout file hash is wrong".
 *
 * A task present in only one run is shown as `unmeasured` on the other side rather than
 * dropped: two runs sampled with different `--tasks` are not comparable task-for-task, and a
 * table that silently intersected them would hide that.
 */
export function TaskDeltaTable({
  comparison,
  onlyMoved,
}: {
  comparison: RunComparison;
  onlyMoved: boolean;
}) {
  const t = useT();
  const tasks = onlyMoved
    ? comparison.tasks.filter((task) => task.move === "gained" || task.move === "lost" || task.move === "partial-moved" || task.move === "unmeasured")
    : comparison.tasks;

  if (!tasks.length) {
    return (
      <EmptyState
        title={onlyMoved ? t("compare.noTasksMoved") : t("compare.noPerTaskScoring")}
        description={
          onlyMoved
            ? t("compare.noTasksMovedHint")
            : t("compare.noPerTaskScoringHint")
        }
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader className="bg-muted/40">
          <TableRow className="hover:bg-transparent">
            <TableHead>{t("compare.columnTask")}</TableHead>
            <TableHead className="w-16 text-right">{comparison.leftRunId}</TableHead>
            <TableHead className="w-16 text-right">{comparison.rightRunId}</TableHead>
            <TableHead className="w-24">{t("compare.columnMove")}</TableHead>
            <TableHead className="w-24 text-right">{t("compare.columnTestRateDelta")}</TableHead>
            <TableHead>{t("compare.columnWhy")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.map((task) => (
            <TableRow key={task.taskId} className="group">
              <TableCell className="font-mono text-xs">
                {task.membership === "both" ? (
                  <Link
                    to={`/runs/${comparison.rightRunId}/tasks/${comparison.rightRound}/${task.taskId}`}
                    className="underline-offset-2 hover:underline"
                    title={t("compare.openTask")}
                  >
                    {task.taskId}
                  </Link>
                ) : (
                  <span
                    title={
                      task.membership === "left-only"
                        ? t("compare.onlyInLeftRun")
                        : t("compare.onlyInRightRun")
                    }
                  >
                    {task.taskId}
                  </span>
                )}
              </TableCell>
              <ScoreCell value={task.leftScore} membership={task.membership === "right-only"} />
              <ScoreCell value={task.rightScore} membership={task.membership === "left-only"} />
              <TableCell>
                <MoveBadge move={task.move} />
              </TableCell>
              <TableCell className="text-right">
                {task.testRateDelta === null ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="cursor-help text-muted-foreground/60">—</span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      {t("compare.testRateTooltip", {
                        left:
                          task.leftTestRate === undefined
                            ? t("record.absent")
                            : (task.leftTestRate ?? t("compare.nullValue")),
                        right:
                          task.rightTestRate === undefined
                            ? t("record.absent")
                            : (task.rightTestRate ?? t("compare.nullValue")),
                      })}
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <DeltaPill value={task.testRateDelta} />
                )}
              </TableCell>
              <TableCell className="max-w-[420px]">
                <div className="space-y-0.5">
                  <DetailLine side="L" detail={task.leftDetail} />
                  <DetailLine side="R" detail={task.rightDetail} />
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="border-t border-border px-3 py-1.5 text-2xs text-muted-foreground">
        {t("compare.thresholdNoteBefore")}
        <span className="font-mono">--trials &gt; 1</span>
        {t("compare.thresholdNoteAfter")}
      </div>
    </div>
  );
}

function ScoreCell({ value, membership }: { value: number | null; membership: boolean }) {
  const t = useT();
  if (membership) {
    return (
      <TableCell className="text-right">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help text-2xs text-muted-foreground">
              {t("compare.notInThisRun")}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {t("compare.notInThisRunHint")}
          </TooltipContent>
        </Tooltip>
      </TableCell>
    );
  }
  if (value === null) {
    return (
      <TableCell className="text-right">
        <Value value={null} />
      </TableCell>
    );
  }
  return (
    <TableCell
      className={cn(
        "text-right tabular font-medium",
        value >= 1 ? "text-pass" : value > 0 ? "text-warn" : "text-fail",
      )}
    >
      {formatScore(value)}
    </TableCell>
  );
}

const MOVE_BADGE: Record<TaskMove, { label: TranslationKey; className: string; icon: typeof Minus }> = {
  gained: { label: "compare.moveGained", className: "border-pass/40 bg-pass/12 text-pass", icon: ArrowUpRight },
  lost: { label: "compare.moveLost", className: "border-fail/40 bg-fail/12 text-fail", icon: ArrowDownRight },
  "still-pass": { label: "compare.moveStillPass", className: "border-border text-muted-foreground", icon: Minus },
  "still-fail": { label: "compare.moveStillFail", className: "border-border text-muted-foreground", icon: Minus },
  "partial-moved": { label: "compare.movePartial", className: "border-warn/40 bg-warn/12 text-warn", icon: Minus },
  unchanged: { label: "compare.moveUnchanged", className: "border-border text-muted-foreground", icon: Minus },
  unmeasured: { label: "compare.moveUnmeasured", className: "border-dashed border-border text-muted-foreground", icon: Minus },
};

function MoveBadge({ move }: { move: TaskMove }) {
  const t = useT();
  const config = MOVE_BADGE[move];
  const Icon = config.icon;
  return (
    <Badge variant="outline" className={cn("gap-1", config.className)}>
      <Icon className="h-3 w-3" />
      {t(config.label)}
    </Badge>
  );
}

function DetailLine({ side, detail }: { side: "L" | "R"; detail: string | null }) {
  const t = useT();
  return (
    <div className="flex items-start gap-1.5">
      <span className="mt-px w-3 shrink-0 font-mono text-2xs text-muted-foreground">{side}</span>
      <span
        className="truncate text-2xs text-muted-foreground"
        title={detail ?? t("compare.noVerdictDetail")}
      >
        {detail ? firstLine(detail, 130) : <Value value={undefined} />}
      </span>
    </div>
  );
}
