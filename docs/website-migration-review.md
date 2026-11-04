# Website branch migration review

## Scope and preserved history

The official site moves from the legacy `zjunlp/HarnessGrad-Hub` repository and
`https://zjunlp.github.io/HarnessGrad-Hub/` to the **`website` branch of
`zjunlp/HarnessGrad`**, published at **https://zjunlp.github.io/HarnessGrad/**.
The imported Hub history remains intact: this migration is a normal child commit of
`698cba29a7704d5b9c7177ca98690f816e9b9685`, not a squash, rebase or orphan rewrite.
The Python platform stays on `main`; these source trees must not be merged together.

Prior `docs/*review.md` files are historical evidence. Their old base URLs, branch names,
worktree paths, dependency symlinks and test counts describe their original runs and were
not rewritten to pretend those reviews happened after migration. The dated announcement's
live Getting started link is updated, but its historical claims and date remain unchanged.
No benchmark data, measured results, charts, run receipts, dependency pins or lockfile changed.
No exporter was run to regenerate evidence.

## Source and URL changes

- Astro keeps `site: https://zjunlp.github.io` and changes `base` to `/HarnessGrad`.
- Starlight content edit links point to
  `https://github.com/zjunlp/HarnessGrad/edit/website/`.
- Local font CSS in both homepage and content styles uses `/HarnessGrad/fonts/`.
- ContentHeader already computes navigation from `import.meta.env.BASE_URL`; it is unchanged.
  Other dynamic base-aware components and platform `main` source links remain valid.
- Browser scripts derive their preview path from the built canonical URL and assert the
  official `/HarnessGrad/` prefix. Content checks additionally assert canonical URLs and
  website-branch edit links. News/Blog listing pages intentionally have no edit links.
- Export output-path documentation now means the website checkout root, not a nested
  `website/` directory. Reproducibility instructions use a clean registered website worktree
  instead of copying a repository to a hardcoded temporary directory.
- Preexisting AppWorld exporter input path
  `/mnt/20t/xubuqiang/datasets/appworld/pool.jsonl` is retained on parent review: this is an
  offline measurement input, not a site URL/output prefix. Export portability is separate
  technical debt; changing its behavior is outside this migration.

## Deployment and manual management

`.github/workflows/deploy.yml` listens to pushes on `website` and supports manual dispatch.
Its primary checkout explicitly uses `${{ github.sha }}` to build the triggering website
commit, rather than accidentally building platform `main` or a moving website head.
Node is pinned to **22.22.1**, pnpm to major 10, and install uses the frozen lockfile.
The workflow builds `dist/`, uploads it with `actions/upload-pages-artifact`, and deploys
through `actions/deploy-pages` under the `github-pages` environment. The shared `pages`
concurrency group does not cancel an already publishing deployment. There is no generated
`gh-pages` branch and no workflow-token request to enable Pages or alter authentication.

The parent maintains the same workflow filename on default branch `main` so GitHub exposes
**Actions → Deploy site → Run workflow**. That entry checks out `website`; a manual dispatch
of this branch's workflow itself should select `website`. Parent integration must ensure
other legacy Pages workflows do not overwrite this site's artifact. An administrator must
configure **Settings → Pages → Source: GitHub Actions** and authorize the deployment refs in
`github-pages` (`website` and `main` for the default-branch manual entry). Legacy Hub redirects
are managed separately and must target `/HarnessGrad/` while preserving useful subpaths.
No push, merge, live deployment, repository setting or network authentication change is part
of this commit.

### Preserve the platform contributors record

A second sparse checkout reads `main:docs/contributors.html` into `platform-record/`.
After building, CI prints that checkout's exact SHA, copies the file unchanged to
`dist/contributors.html`, runs the link checker and uploads the combined artifact. Thus
`/HarnessGrad/contributors.html` remains the old generated platform record, not a new website
interpretation. Its source of truth remains `main`, and changes there need the parent's
main-side workflow trigger/manual deployment. The inspected record is self-contained with
embedded data/images: no sourcebase, external fetch, or relative source links require repair.
`public/contributors/index.html` also preserves the `/HarnessGrad/contributors/` spelling
previously linked by the platform README. Its redirect targets the absolute official URL
`https://zjunlp.github.io/HarnessGrad/contributors.html`,
works without JavaScript via meta refresh and a visible link, and preserves query/hash with JS.
The explicit base also works when the alias is requested without a trailing slash.

## Validation

Executed in `/tmp/hg-website-migration` using Node **v22.22.1** from
`/opt/node-v22.22.1/bin`, with dependencies reused via an uncommitted symlink to
`/tmp/HarnessGrad-Hub/node_modules`; no package install or additional server was needed.

- Parent caught that a root-absolute alias link fails clean build validation before the
  platform record overlay. The alias now uses the full official HTTPS destination for
  redirect and fallback. `pnpm build` remains the supported clean site build; CI subsequently
  overlays and checks the complete artifact. The alias's external destination is separately
  verified in Chromium; the internal link checker does not validate external links.
- Simulated deployment copy of `main:docs/contributors.html`, followed by
  `pnpm check:links`: **PASS**, 20 HTML files, **438 internal URLs**; the alias uses
  an explicit official-origin URL, verified separately below.
- Contributors alias Chromium check: **PASS, 4/4**, with and without JavaScript and with
  and without the trailing slash; JS preserves `?legacy=1#record`, and all versions reach
  the unchanged record heading.
- Content Chromium script: **PASS, 48/48** page/viewport/theme combinations, including
  loaded fonts, navigation/search, accessibility interactions, canonical and edit URL
  assertions; no missing assets or page errors. Screenshots and `results.json`:
  `/tmp/hg-website-migration-content-browser/`. An initial run failed a newly added
  over-strict edit-link presence assertion on the intentionally custom News index;
  excluding News/Blog listing pages from presence (not URL validity) fixed that test.
- Homepage polish Chromium script: **PASS, 8/8** cases, including keyboard interactions,
  scrolling, dark theme, reduced motion, and JavaScript disabled; no page errors.
  Screenshots: `/tmp/hg-website-migration-polish-browser/`.
- Search across built HTML/CSS/XML/JS: no `HarnessGrad-Hub` or `/edit/main` references.
- `git diff --check`: **PASS**.

Build warnings remain: Vite leaves project-base font URLs for runtime resolution (fonts
are served correctly in Chromium); empty i18n/404 content and custom News/Blog index route
precedence warnings are preexisting site structure. Browser evidence is DOM/font/interaction
verification, not human visual approval. Remote Actions execution and live Pages delivery
require parent/operator integration and are not claimed as tested here.
