import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RunsTable } from "./runs-table";
import { certify } from "@/logic/test-support";
import type { RunSummary } from "@/types";

/**
 * The Experiments row, for the states a live API actually returns.
 *
 * Two facts are asserted here because the fixture set alone cannot show them:
 *
 * * a run that is **being written right now** (`runs/full-a`, `runs/full-b`) is a legitimate
 *   row with `points: 0` and a `no curve.jsonl` problem. It must render, not crash and not be
 *   filtered out.
 * * a `test_rate` that was **never recorded** is not a zero. The client reads the field
 *   through `presence.ts`; `null` and absent render as an em dash, and only a real `0` prints
 *   `0.000`.
 */

function summary(overrides: Partial<RunSummary> & { run_id: string }): RunSummary {
  const { run_id, ...rest } = overrides;
  return {
    certify: certify({ run_id }),
    run_id,
    agent_model: "agent-x",
    agent_backend: "openai",
    harness: "loop",
    dataset: "demo",
    improver: null,
    mode: "unified",
    side: null,
    score_kind: null,
    last_score: null,
    last_test_rate: null,
    wall_clock_s: null,
    timestamp_utc: null,
    meta_error: null,
    ...rest,
  };
}

function render(rows: RunSummary[]): string {
  // Radix's Tooltip uses `useLayoutEffect`, which React warns about under `renderToString`.
  // The shipped app is client-only, so the warning is an artefact of the smoke render.
  const originalError = console.error;
  console.error = (...args: unknown[]) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("useLayoutEffect does nothing on the server")
    ) {
      return;
    }
    originalError(...args);
  };
  try {
    return renderToString(
      <MemoryRouter>
        <TooltipProvider>
          <RunsTable rows={rows} validity="all" search="" />
        </TooltipProvider>
      </MemoryRouter>,
    );
  } finally {
    console.error = originalError;
  }
}

const IN_PROGRESS = summary({
  run_id: "full-a",
  certify: certify({
    run_id: "full-a",
    points: 0,
    rounds: [],
    valid: false,
    certified: false,
    problems: ["no curve.jsonl: nothing was measured"],
    final_score: null,
  }),
});
const BEFORE_STAGE_0 = summary({ run_id: "before-stage0", last_test_rate: null });
const REAL_ZERO = summary({ run_id: "real-zero", last_test_rate: 0 });

describe("RunsTable", () => {
  const html = render([IN_PROGRESS, BEFORE_STAGE_0, REAL_ZERO]);

  it("renders a run being written now as a not-measured row, not a crash or a hidden row", () => {
    expect(html).toContain("full-a");
    expect(html).toContain("not measured");
    // still a link into the run detail page
    expect(html).toContain('href="/runs/full-a"');
  });

  it("renders a null test_rate as not recorded and a real 0 as 0.000", () => {
    // Exactly one `0.000` in the table: the run whose test_rate is really zero. `full-a`
    // and `before-stage0` both carry null and must not contribute one.
    expect(html.match(/0\.000/g) ?? []).toHaveLength(1);
    // and the absence is visible as the em dash rather than a number
    expect(html).toContain("—");
  });
});
