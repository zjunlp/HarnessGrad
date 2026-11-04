# Homepage story review

## Direction and rationale

The homepage now follows promise → problem → solution → how → evidence → action. Its opening is “The model is not the whole agent,” followed by a plain explanation of the harness as code organizing tools, context and actions. The full HarnessGrad brand remains in site chrome; the page title is shorter to avoid repeating it in metadata.

`HomepageStory.astro` owns the new semantic HTML and scoped CSS. It has no client script, webfont, canvas, paper SVG, fabricated demo or synthetic score chart. Existing benchmark/method content, sidebar behavior and global `custom.css` are unchanged.

- The five learning modes are a small introductory list, with examples and scopes. Grey modes and a clay Harness accent communicate focus, not superiority. The caption explicitly says update cadences overlap; there are no exact times or ranked speed axes.
- Agent = Harness + Model sits inside an environment boundary. The environment defines tasks, feedback and search boundaries; the harness organizes behavior/search; the fixed model supplies priors.
- The four-step run → feedback → edit → re-evaluate sequence is an ordered HTML list. On phones it becomes one column so reading order and arrows remain unambiguous. It is labeled an illustrative process, not a measured gain.
- The adapter text describes an integration boundary, native execution logic, entrypoint, model/environment bridge, editable surface and supported pairings, without guaranteeing port fidelity or universal compatibility.
- Evidence follows utility. The single evidence section distinguishes the continual-learning ambition from current experiment-running/measurement capabilities. Benchmark pages retain the technical conditions and split details.
- Getting-started received only narrow fixes requested during review: Python 3.12+, clone/venv/install prerequisites, clear mock-fixture labeling and links to real experiments.

## Reference use

The parent supplied verified research summaries for these references. Their structural lessons informed this page, not their branding, metrics or claims:

- [LearnerLabs](https://learnerlabs.ai/): plain story progression from problem through solution, evidence and ongoing work.
- [FlashRSI](https://ruc-nlpir.github.io/FlashRSI/project.html): a short concrete hook; technical evidence after the first explanation.
- [RSIHub](https://export.arxiv.org/abs/2610.32253): distinguish editable resources/recipes from shared evaluation.

Implementation consulted the official [Astro component guide](https://docs.astro.build/en/basics/astro-components/) and [style guide](https://docs.astro.build/en/guides/styling/). Integration wording was checked against the actual platform's `docs/bring-your-own-harness.md` (read-only); installation against its README.

## Validation

- `PATH=/opt/node-v22.22.1/bin:$PATH pnpm run build` — PASS. Astro generated 18 pages and its included link check passed, 472 internal URLs.
- `PATH=/opt/node-v22.22.1/bin:$PATH pnpm run check:links` — PASS independently, same 472 URLs / 18 pages.
- `git diff --check` — PASS.
- Dependencies reused from `/tmp/HarnessGrad-Hub/node_modules`; no lockfile or dependency changes, no new server.
- Parent's browser DOM checks of revised dist at widths 320/390/768/1440: no horizontal overflow, zero clipped story text, equal mode columns and monotonic single-column mobile step order. Four 1440/390 light/dark views: one main landmark, no missing assets or page errors. Screenshots were generated, not visually inspected (the parent's model lacks image input). Parent owns final review and merge; this report does not claim visual acceptance.

## Caveats

The default shell's Node 22.19.0 is below Astro's >=22.22.0 requirement; its first build failed immediately for that reason. The installed Node 22.22.1 resolved it. Successful builds still emit the existing empty-i18n/missing-docs-404 content warnings. The link checker verifies generated local paths, not external URL availability or fragment targets. No benchmark or live-model experiment was needed or run for this presentation-only change. The existing sidebar script remains unchanged; the homepage itself adds no JavaScript.
