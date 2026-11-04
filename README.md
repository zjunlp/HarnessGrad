# HarnessGrad website

The official public site: <https://zjunlp.github.io/HarnessGrad/>.

This is the **`website` branch of [zjunlp/HarnessGrad](https://github.com/zjunlp/HarnessGrad/tree/website)**.
The Python platform stays on `main`; do not merge these independent source trees.
The full history imported from `zjunlp/HarnessGrad-Hub` is preserved. That repository and its
`/HarnessGrad-Hub/` URL are legacy locations, not the deployment target.

What is here:

- **Home** — what the project claims, in one page
- **Getting started** — install, then the local demo
- **Benchmarks** — AppWorld and ALFWorld: score per split, cost, charts
- **Methods** — the eight methods, each with its rule and its results
- **News / Blog** — announcements and posts
- **Contribute** — how to submit results

This branch only presents the platform: nothing here runs an experiment or changes a measurement.
Benchmark and method pages are generated from run receipts by `scripts/export/` — do not edit
those pages by hand. Historical review reports retain their original paths as evidence.

## Work on the website

Use a registered worktree checked out on `website` (or a review branch based on it), separately
from the platform checkout. Requires Node **22.22.1** and pnpm 10:

```bash
pnpm install --frozen-lockfile
pnpm dev      # http://localhost:4321/HarnessGrad/
pnpm build    # dist/, then scripts/check-links.mjs
python3 scripts/check-content-taste.py --output /tmp/hg-website-content-browser
python3 scripts/check-homepage-polish-v3.py
```

The browser checks need existing Python Playwright and Chromium; they serve built files through
request interception, without starting a server or installing packages. Navigation derives its
base from Astro; font CSS and hand-authored absolute links must also use `/HarnessGrad/`.
Content edit links target `https://github.com/zjunlp/HarnessGrad/edit/website/`.

## Deploy and manage

- A push to **`website`** runs `.github/workflows/deploy.yml`, checks out that exact commit,
  builds the site, and uploads/deploys `dist/` with GitHub Actions Pages. No generated branch.
- For a manual deployment use **Actions → Deploy site → Run workflow** in `zjunlp/HarnessGrad`.
  The default-branch entry workflow checks out `website`; when choosing the website workflow
  directly, select **`website`**, not an unrelated ref.
- An administrator must set **Settings → Pages → Source: GitHub Actions** and authorize
  `website` plus the default-branch manual entry in the `github-pages` environment. The
  workflow does not change authentication, permissions settings or repository settings.
- Deployment copies the platform `main` branch's `docs/contributors.html` unchanged into
  `dist/contributors.html`, preserving the old generated record at `/HarnessGrad/contributors.html`.
  Its platform commit is printed in the build log. Maintain that record on `main`, not here.
  The former README spelling `/HarnessGrad/contributors/` redirects to the same record.
- The legacy Hub repository redirect is managed separately; do not deploy this site back there.

See [migration review](docs/website-migration-review.md) for validation and deployment boundaries.
