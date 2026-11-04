# Content taste review

## Scope and decisions

- Worktree `/tmp/hg-hub-docs-taste`, branch `codex/hub-docs-taste`, based on `83ad49793f73aca871457f3e5e3fc9ed861096cd`. No merge or push.
- The approved homepage, its fonts, copy, illustrations and interactive learning cycle are untouched. The configuration now uses the exact brand capitalization **HarnessGrad: Harness as A Continual Learner**.
- Replaced the obsolete warm-paper/clay content stylesheet with a complete white/charcoal/teal reading system matching the homepage. Chakra Petch headings include Starlight's actual `h1#_top`, not only markdown headings; EB Garamond is restricted to editorial prose. Navigation, search, theme controls, benchmark controls and tables remain system sans; code remains 14px system mono.
- Shared `ContentHeader.astro` provides a short wordmark and original rising mark, Home / Docs / News / Blog / GitHub links with base-aware current state, and the **native Starlight Search and ThemeSelect**. The long brand stays in metadata, not a mobile-overflowing header. On narrow docs pages the native sidebar supplies its existing theme select. The native mobile-menu popover remains owned by Starlight's PageFrame, including keyboard behavior and focus/inert management.
- Native docs sidebar, mobile menu, TOC, anchors, Pagefind search, Expressive Code copy buttons, edit links and pagination remain intact. A minimal PageTitle override adds `tabindex="-1"` to the otherwise identical title so the native SkipLink actually transfers keyboard focus into the article (the upstream heading was not focusable). The existing optional desktop sidebar-collapse control now updates its accessible name and is not created on sidebar-free journal pages.
- News and Blog have genuine editorial indexes built from the content collection, rather than a full-article dump or a blank page. News currently contains exactly one historical announcement; its summary is taken from the original first paragraph, with a clear date/title/read link hierarchy. No article text was rewritten. The empty blog explicitly says that no posts have been published, supplies docs/news links, and has a small original notebook SVG carrying the rising mark motif. Future posts populate the shared index automatically.
- Journal articles keep the plugin's metadata, RSS, tags, structured data and article body while dropping the redundant blog sidebar; their native article TOC remains. The indexes use official StarlightPage rather than duplicating search/theme plumbing.
- Verified unused `.hg-grid`, `.hg-curve`, `.hg-links` selectors had no callers outside the old stylesheet before deleting that furniture. `BenchmarkFilter.astro` and `BenchmarkChart.astro` were not edited; their table/chart/filter styles and behavior remain local.
- No packages installed and no servers started. Dependencies are a local symlink to `/tmp/HarnessGrad-Hub/node_modules` (not committed).

## Sources inspected before implementation

- Repository `AGENTS.md`, `astro.config.mjs`, `src/styles/custom.css`, `src/styles/homepage.css`, `src/pages/index.astro`, benchmark components, existing announcement.
- Installed starlight-blog configuration, middleware, routes, index integration, MarkdownContent override, Page, Posts and Metadata components; installed Starlight Header, PageFrame, MobileMenuToggle, Search, ThemeSelect and StarlightPage.
- [Official Starlight component overrides](https://starlight.astro.build/guides/overriding-components/): reuse native components and access route data rather than removing useful behavior.
- [Astro components](https://docs.astro.build/en/basics/astro-components/) and [styles](https://docs.astro.build/en/guides/styling/).

## Repeatable verification

```sh
PATH=/opt/node-v22.22.1/bin:$PATH npm run build
python3 scripts/check-content-taste.py
python3 scripts/check-homepage-polish-v3.py
# Optional, against the parent's existing preview (starts no server):
python3 scripts/check-content-taste.py --url http://127.0.0.1:4325
```

The content check uses actual headless Chromium. Without `--url`, Playwright serves the built files through request fulfillment, including native Pagefind modules and search data. It checks **48 page/view/theme combinations**: homepage, Getting started, News, Blog, original news article, and ALFWorld benchmark at **320 / 390 / 768 / 1440** pixels in **light and dark**. Checks cover loaded local fonts, one h1, current navigation, document overflow, honest empty state, genuine announcement date/count, actual search query results and keyboard modal close/focus restoration, mobile menu keyboard open/close and inert management, desktop collapse accessibility, article metadata/TOC, native code-copy keyboard reachability, theme changes and non-serif data tables. Screenshots and machine-readable results go to `/tmp/hg-content-taste-browser` and are not committed.

Initial build: **PASS**, 18 generated pages, 438 internal links validated. Initial browser matrix: **48/48 PASS**, no JS errors or missing local assets. Second browser matrix including copy/theme/TOC checks: **48/48 PASS**, zero errors. Existing homepage interaction regression: **8/8 PASS**, including normal widths, reduced motion, no-JavaScript and dark mode. Final matrix including corrected skip-link focus on every content route: **48/48 PASS**, zero errors. `git diff --check` and Python syntax compilation passed. Exact baseline diff checks confirmed no change to homepage source/styles/story/learning cycle, historical docs/news content, or benchmark components. Parent independently reported a 42-view matrix passing against live preview port 4325 and confirmed the corrected skip-link focus, native mobile menu/theme and 438 links.

Expected build warnings: `/news` and `/blog` explicit index pages take priority over the plugin's optional catch-all indexes; public font URLs are intentionally resolved at runtime under the GitHub Pages base; existing empty i18n and missing custom 404 notices remain. No warning indicates an asset missing in Chromium.

## Review limitations

Browser DOM, loaded fonts, keyboard interactions, request results and layout bounds were actually tested. Screenshots were captured for parent/operator visual review; this worker did not claim image-based visual inspection. Parent owns preview port 4325, independent review and any later merge.
