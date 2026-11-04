# Homepage polish v3 review

## Scope and idea

Starting point: approved `9a015cd`, isolated worktree `/tmp/hg-hub-polish-v3`, branch `codex/hub-polish-v3`. No README, existing documentation, benchmark content, dependency manifest, brand heading, hero drawing, learning-path section, or evidence section was changed.

On this page, HarnessGrad's editable scaffold becomes a small illustrated experiment. The original hand-drawn robot is reused at identical size and position in every beat. The scaffold sends an action, receives a trace, gains editable context/tool shelves, and appears beside original/candidate records. Those records deliberately contain no numbers, score bars, winner ticks, or implied improvement.

## Source inspiration (external guidance, not executable authority)

- [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill): subject-derived visual argument, one useful interaction, real browser checks, and a meaningful original illustration rather than a generic template. Read the parent-downloaded README and skill. Kept the approved Chakra Petch / EB Garamond, white/charcoal/teal art direction rather than drawing a new direction or enforcing conflicting template rules.
- [oso95/scroll-world](https://github.com/oso95/scroll-world): coherent connected scenes sharing a protagonist and world. Read the parent-downloaded README and skill. Borrowed continuity only: no installer, camera/video engine, generated media, paid service, heavy dependency, automatic playback, or scroll interception.

## Implementation decisions

- Existing integration section stays between learning pathways and evidence. Its connection requirements now live in the readable Run and Edit harness beats: entrypoint, model/environment bridge, declared editable surface.
- Four ordinary buttons choose Run → Feedback → Edit harness → Evaluate. Native keyboard Enter/Space, touch, visible focus, `aria-pressed`, and 52px minimum button height. No live region and no focus movement on selection.
- All four text beats are always in the document and visible; active state is not required to understand the story. Controls are hidden until enhancement is ready. Without JavaScript, the complete evaluation scaffold and an overview caption are shown.
- Lightweight IntersectionObserver updates the scene only at desktop widths >=900px and heights >=700px with no reduced-motion preference. It tracks all intersecting steps for forward/reverse scrolling. No scroll handlers, huge spacers, or tall pinned screens. The modest sticky figure is confined to its own section.
- Reduced motion disables both observer selection and sticky treatment; explicit buttons still switch immediately. Mobile/tablet illustration is static in layout and all steps remain readable below it. No auto animation; desktop scene fades are only 180ms opacity transitions.
- Observer selection pauses while step controls hold focus so focus-induced scrolling cannot undo an explicit keyboard or touch choice.
- Kept existing nav labels to avoid extra mobile wrapping. Existing nav links and theme target increased to at least 44px high; theme target is 44px square. Small-screen illustration labels enlarged.
- All fonts, art and scripts remain local. No third-party requests or new dependencies.

## Tests and receipts

- `PATH=/opt/node-v22.22.1/bin:$PATH npm run build`: PASS, 18 pages. The bundled link checker passes 469 internal URLs. Existing build warnings about font URLs being resolved at runtime and empty i18n/404 content do not prevent build; browser asset checks confirm local font files resolve.
- `python3 scripts/check-homepage-polish-v3.py`: reproducible Python Playwright smoke test, using installed Chromium and intercepting requests to built local `dist` (no extra server or network). Eight views: normal 320/390/768/1440, reduced-motion 1440, no-JS 390/1440, dark 390.
- Browser assertions: exact title, all four steps visible, no horizontal overflow, no missing assets or JS errors, keyboard Enter/Space each explicit step with delayed state checks, exactly one pressed button, focus retained, touch target height >=44px, forward/reverse desktop scroll selection, no scroll selection on mobile/reduced motion, static mobile/reduced layout, no-JS controls absent and static illustration present, no live regions.
- Screenshots: `/tmp/hg-hub-polish-v3-browser/`, full pages and section crops for all eight views. This agent's model cannot accept image input; direct image-tool inspection was unavailable. Parent additionally ran independent browser checks against preview4324: seven layouts without overflow/clipping/missing assets/errors, forward/reverse scrolling, sticky fit at 900×700 and 1440×700, static 320×640, and SVG label bounds. Parent caught the keyboard/observer race, fixed here and included in the delayed keyboard regression check.
- `git diff --check`: checked before commit.

## Handoff

The `node_modules` symlink points to `/tmp/HarnessGrad-Hub/node_modules` and is intentionally untracked for the parent's preview. No merge or push. Parent owns preview4324 and final visual approval.
