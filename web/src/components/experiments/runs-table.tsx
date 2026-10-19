import { useMemo, useState } from "react";
import { Link } from "react-router";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  CircleAlert,
  ExternalLink,
  Info,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/common/states";
import { Value } from "@/components/common/presence-value";
import { EM_DASH, formatDuration, formatScore, formatTimestamp, shortSha } from "@/lib/format";
import { badgeFor, matchesSearch, matchesValidity, type ValidityFilter } from "@/logic/certify";
import { presenceOf } from "@/logic/presence";
import { useT, type Translate } from "@/i18n";
import { cn } from "@/lib/utils";
import type { CertifyBadge, BadgeTone } from "@/logic/certify";
import type { RunSummary } from "@/types";

/**
 * The Experiments table: every run, dense, sortable, filterable.
 *
 * This is the screen that answers *"which of our 85 runs can I actually quote?"* — so the
 * first column after the id is the certification badge, and the badge is never just a
 * colour: hovering it names the problems or the reason it is only `valid`. The score column
 * is deliberately not first, because a quotable-looking number that fails `certify()` is
 * the thing this table exists to catch.
 *
 * TanStack Table is used for the row model (sorting and filtering); the markup is plain
 * `shadcn/ui` table primitives and the typography is fixed here rather than left to the
 * default so the dense view stays legible.
 */

const TONE_VARIANT: Record<BadgeTone, "pass" | "info" | "warn" | "fail" | "muted"> = {
  pass: "pass",
  info: "info",
  warn: "warn",
  fail: "fail",
  muted: "muted",
};

function StatusBadge({ badge }: { badge: CertifyBadge }) {
  const t = useT();
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={TONE_VARIANT[badge.tone]} className="cursor-help">
          {t(badge.labelKey)}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-md space-y-1.5">
        <p className="text-xs">{t(badge.explanationKey)}</p>
        {badge.problems.length ? (
          <ul className="list-disc space-y-0.5 pl-4 text-2xs text-fail">
            {badge.problems.slice(0, 4).map((p) => (
              <li key={p}>{p}</li>
            ))}
            {badge.problems.length > 4 ? (
              <li>{t("experiments.andMore", { n: badge.problems.length - 4 })}</li>
            ) : null}
          </ul>
        ) : null}
        {badge.certificationNote ? (
          <p className="text-2xs text-muted-foreground">{badge.certificationNote}</p>
        ) : null}
      </TooltipContent>
    </Tooltip>
  );
}

/** The rows' pre-computed view model, so the table's accessors are total functions. */
interface RunRow {
  summary: RunSummary;
  badge: CertifyBadge;
  rounds: number;
  lastRound: number | null;
}

const columnHelper = createColumnHelper<RunRow>();

/**
 * The columns, built per render so a header can be translated.
 *
 * A header that held a label string would be frozen at module scope and could never change
 * with the language; taking `t` as an argument keeps the key constants where the column is
 * declared and the label where the reader is.
 */
function columnsFor(t: Translate): ColumnDef<RunRow>[] {
  return [
    columnHelper.accessor((row) => row.summary.run_id, {
      id: "run_id",
      header: t("experiments.colRun"),
      cell: (info) => {
        const summary = info.row.original.summary;
        return (
          <div className="flex items-center gap-1.5">
            <Link
              to={`/runs/${summary.run_id}`}
              className="font-mono text-xs font-medium text-foreground underline-offset-2 hover:underline"
            >
              {summary.run_id}
            </Link>
            {summary.meta_error ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <CircleAlert className="h-3 w-3 text-warn" />
                </TooltipTrigger>
                <TooltipContent>{summary.meta_error}</TooltipContent>
              </Tooltip>
            ) : null}
          </div>
        );
      },
      sortingFn: "alphanumeric",
    }),
    columnHelper.accessor((row) => row.badge.level, {
      id: "status",
      header: t("experiments.colStatus"),
      cell: (info) => <StatusBadge badge={info.row.original.badge} />,
      sortingFn: (a, b) => rank(a.original.badge.level) - rank(b.original.badge.level),
    }),
    columnHelper.accessor((row) => row.rounds, {
      id: "rounds",
      header: t("experiments.colRounds"),
      cell: (info) => {
        const row = info.row.original;
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="tabular cursor-help">
                {row.rounds}
                {row.lastRound !== null ? (
                  <span className="text-muted-foreground"> (0–{row.lastRound})</span>
                ) : null}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {t("experiments.roundsMeasured", {
                rounds: row.badge.rounds.length
                  ? row.badge.rounds.join(", ")
                  : t("experiments.roundsNone"),
              })}
            </TooltipContent>
          </Tooltip>
        );
      },
    }),
    columnHelper.accessor((row) => row.summary.certify.points, {
      id: "points",
      header: t("experiments.colPoints"),
      cell: (info) => <span className="tabular">{info.getValue()}</span>,
    }),
    columnHelper.accessor((row) => row.summary.last_score, {
      id: "last_score",
      header: t("experiments.colLastScore"),
      cell: (info) => {
        const badge = info.row.original.badge;
        const score = info.getValue();
        return (
          <span className="flex items-center gap-1">
            <span className={cn("tabular font-medium", score !== null && score > 0 && "text-foreground")}>
              {formatScore(score)}
            </span>
            {badge.level === "invalid" || badge.level === "tampered" ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info className="h-3 w-3 text-fail" />
                </TooltipTrigger>
                <TooltipContent>
                  {t("experiments.notQuotable", { label: t(badge.labelKey) })}
                </TooltipContent>
              </Tooltip>
            ) : null}
          </span>
        );
      },
    }),
    columnHelper.accessor((row) => row.summary.last_test_rate, {
      id: "last_test_rate",
      header: "test_rate",
      cell: (info) => {
        const value = info.getValue();
        // The three-state rule, read through `presence.ts` rather than `=== null`: a run
        // whose last point predates stage 0 carries *no* `test_rate`, and the API reports
        // that as `null`. Either way it is "not recorded" and must never render as `0.000`.
        const presence = presenceOf(
          info.row.original.summary as unknown as Record<string, unknown>,
          "last_test_rate",
        );
        const known = presence === "present" && typeof value === "number";
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn("tabular cursor-help", !known && "text-muted-foreground/60")}>
                {known ? formatScore(value) : EM_DASH}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {known ? t("experiments.testRateKnown") : t("experiments.testRateUnknown")}
            </TooltipContent>
          </Tooltip>
        );
      },
    }),
    columnHelper.accessor((row) => row.summary.agent_model, {
      id: "agent_model",
      header: t("experiments.colAgentModel"),
      cell: (info) => <Value value={info.getValue()} fieldName="agent_model" />,
    }),
    columnHelper.accessor((row) => row.summary.harness, {
      id: "harness",
      header: t("experiments.colHarness"),
      cell: (info) => <span className="font-mono text-2xs">{info.getValue() ?? "—"}</span>,
    }),
    columnHelper.accessor((row) => row.summary.dataset, {
      id: "dataset",
      header: t("experiments.colDataset"),
      cell: (info) => <span className="font-mono text-2xs">{info.getValue() ?? "—"}</span>,
    }),
    columnHelper.accessor((row) => row.summary.improver, {
      id: "improver",
      header: t("experiments.colImprover"),
      cell: (info) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help text-2xs">{info.getValue() ?? "—"}</span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {t("experiments.improverHint")}
          </TooltipContent>
        </Tooltip>
      ),
    }),
    columnHelper.accessor((row) => row.summary.timestamp_utc, {
      id: "timestamp_utc",
      header: t("experiments.colLastPoint"),
      cell: (info) => (
        <span className="whitespace-nowrap text-2xs text-muted-foreground">
          {formatTimestamp(info.getValue())}
        </span>
      ),
    }),
    columnHelper.accessor((row) => row.summary.wall_clock_s, {
      id: "wall_clock_s",
      header: t("experiments.colWallClock"),
      cell: (info) => (
        <span className="tabular text-2xs text-muted-foreground">
          {formatDuration(info.getValue())}
        </span>
      ),
    }),
    columnHelper.accessor((row) => row.summary.certify.platform_content_sha, {
      id: "platform_content_sha",
      header: t("experiments.colPlatformSha"),
      cell: (info) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help font-mono text-2xs text-muted-foreground">
              {shortSha(info.getValue())}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {t("experiments.platformShaHint")}
          </TooltipContent>
        </Tooltip>
      ),
    }),
  ] as ColumnDef<RunRow>[];
}

function rank(level: CertifyBadge["level"]): number {
  return { certified: 0, valid: 1, "no-points": 2, invalid: 3, tampered: 4 }[level];
}

export function RunsTable({
  rows,
  validity,
  search,
}: {
  rows: RunSummary[];
  validity: ValidityFilter;
  search: string;
}) {
  const t = useT();
  const [sorting, setSorting] = useState<SortingState>([{ id: "run_id", desc: false }]);
  const columns = useMemo(() => columnsFor(t), [t]);

  const data = useMemo<RunRow[]>(
    () =>
      rows.map((summary) => ({
        summary,
        badge: badgeFor(summary.certify),
        rounds: summary.certify.rounds.length,
        lastRound: summary.certify.rounds.length
          ? Math.max(...summary.certify.rounds)
          : null,
      })),
    [rows],
  );

  const filtered = useMemo(
    () =>
      data.filter(
        (row) => matchesValidity(row.badge, validity) && matchesSearch(row.summary, search),
      ),
    [data, validity, search],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  if (!data.length) {
    return (
      <EmptyState
        title={t("experiments.emptyTitle")}
        description={t("experiments.emptyDescription")}
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-lg border border-border">
        <Table>
          <TableHeader className="bg-muted/40">
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="hover:bg-transparent">
                {headerGroup.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead key={header.id} className="whitespace-nowrap">
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-foreground"
                        title={t("experiments.sortBy", {
                          column: String(header.column.columnDef.header),
                        })}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sorted === "asc" ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : sorted === "desc" ? (
                          <ArrowDown className="h-3 w-3" />
                        ) : (
                          <ChevronsUpDown className="h-3 w-3 opacity-40" />
                        )}
                      </button>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between text-2xs text-muted-foreground">
        <span>
          {t(
            validity !== "all" || search
              ? "experiments.runsShownFiltered"
              : "experiments.runsShown",
            { shown: filtered.length, total: data.length },
          )}
        </span>
        <Link
          to="/compare"
          className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
        >
          {t("experiments.compareTwoRuns")}
          <ExternalLink className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}
