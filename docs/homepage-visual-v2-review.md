# Homepage visual v2 review

## Design plan (recorded before implementation)

Homepage-only replacement; documentation keeps Starlight and its existing CSS. Exact primary heading: **HarnessGrad: Harness as A Continual Learner**.

Tokens: canvas `#ffffff`, ink `#252b29`, secondary `#59635e`, rule `#dce3df`, teal `#2f6e62`, pale teal `#edf5f1`. Dark mode uses `#17201d`, `#edf3ef` ink, and `#9dd0be` accent.

Typography follows the user-provided measured learnerlabs.ai reference: Chakra Petch 600 at 59px / 1.08 / -.035em for desktop h1; Chakra Petch 500 at 40px / 1.16 / -.03em for h2; EB Garamond 400 at 22px / 1.48, max 510px prose. Tiny technical labels use a system sans, not monospace. Fonts served locally.

Layout: left-aligned editorial two-column spreads with ample whitespace, maximum 1160px content. Brand and original friendly agent-scaffold drawing in hero; prose and compact five learning lanes; prose and adapter/experimental loop; compact evidence/CTA. Mobile stacks, without horizontal scrollers or hidden navigation.

```
brand                         docs / news / GitHub / theme
exact h1                      friendly agent in environment
fixed model, evolving harness original soft-line illustration
get started

five ways / overlooked learner context ~ short tokens
short focused explanation      memory —— persistent trace
                               skills ▱ distilled blocks
                               weights · parameter network
                               harness ⤴ editable graph

bring your own harness         adapter: entrypoint / bridge / surface
concise explanation            run → feedback → edit → evaluate

evidence and next experiment   real results / getting started
```

Anti-template review: rejected serif display, cream/clay, five equal cards, decorative numbered eyebrow labels, and repeated philosophical disclaimers. The memorable element is a small original friendly scientific doodle (rounded agent inside adjustable scaffolding), reflecting the user's newly clarified affection for learnerlabs illustrations; not a copy of their animals/art. Motifs convey specific learning surfaces. No unsupported gains. No continuous motion is necessary.

References read: parent-provided copies of [Anthropic frontend design](https://github.com/anthropics/skills/tree/main/skills/frontend-design) and [Impeccable](https://github.com/pbakaus/impeccable), as external design guidance only (no installers). Astro routing documentation read. Reference measurements supplied by parent/user; no claim of visual screenshot inspection.

## Implementation, licenses and validation

Implemented standalone `src/pages/index.astro`, homepage-only `src/styles/homepage.css`, and replaced `HomepageStory.astro`. Removed the former Starlight `src/content/docs/index.mdx` homepage to avoid a competing route. Shared config, docs styles/routes, benchmark data and README are unchanged. Existing four-bar brand mark geometry is reused inline, with homepage teal styling only. No animation; reduced-motion users see the same complete illustration.

### Local fonts and licenses

Unmodified TTFs retrieved from Google's CSS API (`https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600&family=EB+Garamond:wght@400&display=swap`):

- Chakra Petch Medium 500: `https://fonts.gstatic.com/s/chakrapetch/v13/cIflMapbsEk7TDLdtEz1BwkebIlFQA.ttf` (70,252 bytes).
- Chakra Petch SemiBold 600: `https://fonts.gstatic.com/s/chakrapetch/v13/cIflMapbsEk7TDLdtEz1BwkeQI5FQA.ttf` (70,240 bytes).
- EB Garamond Regular 400: `https://fonts.gstatic.com/s/ebgaramond/v33/SlGDmQSNjdsmc35JDF1K5E55YMjF_7DPuGi-6_RUAw.ttf` (~380 KiB).

Both SIL Open Font License 1.1 texts included beside the fonts in `public/fonts/Chakra-Petch-OFL.txt` and `public/fonts/EB-Garamond-OFL.txt`. Copyright respectively 2018 Chakra Petch Project Authors and 2017 EB Garamond Project Authors. Parent obtained authoritative texts through `https://api.github.com/repos/google/fonts/contents/ofl/chakrapetch/OFL.txt` and `https://api.github.com/repos/google/fonts/contents/ofl/ebgaramond/OFL.txt` after direct raw/CDN requests failed. Font family/name metadata checked by parent. No third-party runtime font requests or installers. CSS font URLs deliberately match the existing configured `/HarnessGrad-Hub` base; if base changes these URLs must change with it.

### Validation receipts

- `PATH=/opt/node-v22.22.1/bin:$PATH npm run build` passed twice; latest build 18 pages, link checker **469 internal URLs, OK**. Dependencies temporarily symlinked to `/tmp/HarnessGrad-Hub/node_modules`; untracked symlink retained for parent's preview, never committed.
- No homepage route collision. Existing Starlight warnings about empty i18n collection and missing custom 404 entry remain. Vite warns the base-prefixed public font URLs resolve at runtime; browser verification confirms all three load successfully.
- Chromium/Playwright rendered built assets at **320, 390, 768, 1440px light**, plus **390 and 1440px dark**. Exact normalized h1 in all six views; one main landmark; no document horizontal overflow, broken fragment link or SVG text outside viewBox. All three font faces report `loaded`. Desktop h1 59px, small-screen h1 36px. Cadence labels are 12px, including 320px.
- No missing assets or page errors. Theme button works and updates its accessible label. Clipped-text detector flags only the five intentionally screen-reader-only learning descriptions, not visible text.
- Native keyboard links/button, skip link, focus-visible outlines, semantic headings, named navigation, SVG title/description, and redundant diagram explanation included. Parent independently tested preview routes home/docs/AppWorld/blog return 200.
- `git diff --check` passed. README and shared documentation stylesheet unchanged.
- Screenshots captured by browser test to `/tmp/hg-visual-v2-review/`. **Not visually inspected by this model**; tests are DOM/font/geometry evidence, not a visual approval. Human/parent preview review remains necessary.

Parent-managed preview is on port 4323. No merge, push or deployment performed.
