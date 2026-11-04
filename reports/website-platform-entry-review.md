# Website platform entry review

## Change

The platform's Pages workflow now builds the repository's `website` branch in `site/` with `pnpm exec astro build`, then overlays `docs/contributors.html` from the triggering platform commit (`github.sha`) into `site/dist` and runs `pnpm --dir site check:links` before uploading the full artifact. The website's `pnpm build` runs link checking too early for the contributor alias, so it is intentionally split into these steps. Push triggers are limited to main changes to that record or the deployment workflow; manual dispatch remains available. Both website and platform workflows serialize with the `pages` concurrency group and deploy to `github-pages`. The old redirect workflow is removed; `site-redirect/` remains historical and is not deployed by this workflow. No site source is added to the platform branch.

The website source config sets base `/HarnessGrad`; its `contributing/` page is distinct from the old `contributors/` link. No `contributors/` source route was found; the actual contributor record is staged as `contributors.html` by both deployment workflows. README and generator links now use that route. Generator `--write` changed only the generated README link and removed its redundant duplicate; the SVGs, contributor Markdown and HTML had no diff. The integrity exclusion remains unchanged; only its explanation was updated. Existing historical redirect pages and CI selection behavior remain intact.

## Verification

Focused pytest covering documentation links, contributors, CI selection and workflows, deployment, integrity, tau2 integrity, isolation, container and package compatibility: **248 passed, 5 skipped**. After removing the duplicate contributor link, reran documentation links, contributors and deployment tests: **23 passed**. `python3 tools/contributors.py --check` and `git diff --check` passed. The deployment guard is a textual regression check; it does not execute GitHub Actions or validate a live Pages artifact.

## Operational sequencing

The website branch must exist remotely before the platform workflow runs. Push the website branch first, then the platform change; its website workflow may run immediately, so avoid concurrent triggers of the old redirect workflow until the platform update lands. The `github-pages` environment must permit both branches. No push, merge or deployment was performed by this task. These checks do not constitute a live GitHub Pages deployment.
