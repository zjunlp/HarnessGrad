import { readFileSync } from "node:fs";
import path from "node:path";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { App } from "@/App";
import { queryKeys } from "@/hooks/queries";
import type { LaunchOptions, RoundDiff, RunBundle, RunSummary, TaskPage } from "@/types";

/**
 * Every screen mounts, over the real fixture data.
 *
 * `tsc` proves the types line up and the logic suites prove the derivations; neither proves
 * that a screen renders. This suite server-renders the whole component tree for each route
 * with a QueryClient pre-seeded from `src/fixtures/data/`, which catches the class of error
 * that only appears at render time — a bad Radix composition, a `null` dereference behind a
 * guard that looked total, a chart that cannot lay itself out.
 *
 * Seeding the cache (rather than letting the queries resolve) is what makes the assertions
 * meaningful: the screens render their *loaded* state, not their skeletons.
 */

const DATA = path.resolve(process.cwd(), "src/fixtures/data");
const load = <T,>(name: string): T =>
  JSON.parse(readFileSync(path.join(DATA, name), "utf8")) as T;

const runs = load<RunSummary[]>("runs-index.json");
const dsDgm = load<RunBundle>("run-ds-dgm.json");
const dsRrsi = load<RunBundle>("run-ds-rrsi.json");
const cobol = load<TaskPage>("task-ds-dgm-r0-cobol-modernization.json");
const launchOptions = load<LaunchOptions>("launch-options.json");

function render(route: string): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.runs, runs);
  client.setQueryData(queryKeys.run("ds-dgm"), dsDgm);
  client.setQueryData(queryKeys.run("ds-rrsi"), dsRrsi);
  // The patch is its own request now (`GET /api/runs/:id/rounds/:n/diff`). The fixture
  // bundles still carry it inline, so seed it under the diff query key the panels read.
  for (const [id, bundle] of [
    ["ds-dgm", dsDgm],
    ["ds-rrsi", dsRrsi],
  ] as const) {
    const diffs = bundle.diffs as unknown as Record<string, RoundDiff>;
    for (const [round, diff] of Object.entries(diffs)) {
      client.setQueryData(queryKeys.roundDiff(id, Number(round)), diff);
    }
  }
  client.setQueryData(queryKeys.task("ds-dgm", 0, "cobol-modernization"), cobol);
  client.setQueryData(queryKeys.launchOptions, launchOptions);
  // Radix's Tooltip uses `useLayoutEffect`, which React warns about under `renderToString`.
  // The shipped app is client-only, so the warning is an artefact of this smoke test, not a
  // defect to fix; it is filtered so a real console error stays visible in the test output.
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
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  } finally {
    console.error = originalError;
  }
}

describe("Experiments", () => {
  const html = render("/experiments");

  it("renders the runs table with the certification vocabulary", () => {
    expect(html).toContain("Experiments");
    expect(html).toContain("Quotable");
    expect(html).toContain("certified");
    expect(html).toContain("ds-dgm");
  });

  it("shows the run whose number is a claim, and the one with nothing measured", () => {
    expect(html).toContain("smoke-a");
    expect(html).toContain("invalid");
    expect(html).toContain("codex-task-dgm");
    expect(html).toContain("not measured");
  });
});

describe("Run detail", () => {
  const html = render("/runs/ds-dgm");

  it("renders the header, the curve section and the captured-flat-score warning", () => {
    expect(html).toContain("ds-dgm");
    expect(html).toContain("Curve");
    expect(html).toContain("Measured states");
    // the second series and the masking warning are the two things this screen exists for
    expect(html).toContain("test_rate");
    expect(html).toContain("but the set of passing tasks does");
  });

  it("offers the diff and identity tabs, and names the agent and the missing improver", () => {
    // Radix renders only the active tab panel on the server, so the assertion is on the tab
    // triggers and on the header — the identity panel itself is covered by the identity logic.
    expect(html).toContain("Candidate diff");
    expect(html).toContain("Identity &amp; config");
    expect(html).toContain("qwen3.5-9b-local");
    // ds-dgm predates `run_meta.improver`; the header says so rather than guessing the method
    expect(html).toContain("no improver recorded");
    expect(html).toContain("Score lift");
  });
});

describe("Compare", () => {
  it("renders the default comparison of the two fixture runs", () => {
    const html = render("/compare");
    expect(html).toContain("What moved");
    expect(html).toContain("Per-task delta");
    expect(html).toContain("Record diff");
    expect(html).toContain("Candidate diffs");
    // the patches come from the seeded `GET /api/runs/:id/rounds/:n/diff` queries, so the
    // loaded state renders the view-mode buttons rather than the loading skeleton
    expect(html).toContain("Both patches");
  });

  it("states the hidden change in words when the score is flat and tasks moved", () => {
    // ds-dgm round 0 -> round 1: score 0.1 at both, cobol-modernization lost, fix-git gained
    const html = render("/compare?left=ds-dgm&right=ds-dgm&leftRound=0&rightRound=1");
    expect(html).toContain("Total score unchanged");
    expect(html).toContain("cobol-modernization");
    expect(html).toContain("fix-git");
  });
});

describe("Task detail", () => {
  const html = render("/runs/ds-dgm/tasks/0/cobol-modernization");

  it("renders the verdict and the trace as a timeline", () => {
    expect(html).toContain("cobol-modernization");
    expect(html).toContain("Trace timeline");
    expect(html).toContain("step(s)");
    expect(html).toContain("model reply");
    expect(html).toContain("$ command");
  });

  it("shows the per-test tally beside the binary score", () => {
    expect(html).toContain("detail — the check");
    expect(html).toContain("Harness exit code");
  });
});

describe("Launch", () => {
  const html = render("/launch");

  it("renders the form, the argv preview and the env-derived panel", () => {
    expect(html).toContain("Launch");
    expect(html).toContain("not submitted anywhere");
    expect(html).toContain("python3 driver.py");
    expect(html).toContain("Read from the environment, not from this form");
  });

  it("never exposes a secret's value", () => {
    const secrets = launchOptions.env_derived.filter((field) => field.secret);
    expect(secrets.length).toBeGreaterThan(0);
    for (const field of secrets) expect(field.value).toBeNull();
    expect(html).toContain("value hidden");
    // and nothing credential-shaped reaches the render at all
    expect(html).not.toMatch(/sk-[A-Za-z0-9_-]{16,}/);
  });
});

describe("unknown route", () => {
  it("renders an empty state that names the five screens", () => {
    const html = render("/nothing-here");
    expect(html).toContain("No such screen");
    expect(html).toContain("Back to Experiments");
  });
});
