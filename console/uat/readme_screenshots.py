#!/usr/bin/env python3
"""Capture the README's workbench screenshots from a served console.

Why these two frames
--------------------
The README shows what the platform *keeps*: every round's edit as a diff, and the curve
the rounds produced. A screenshot of the editor with the round selector on the left, the
diff in the middle and the assistant on the right is the shortest honest answer to "what
does 'a harness that improves' look like as an artifact" -- and it is a real console over
a real run, not a mock-up.

Usage
-----
    python3 console/uat/readme_screenshots.py --url http://127.0.0.1:4484 \
        --run appworld-study-sica_ci-20261019T1816Z --out .github/assets

The run must be one the console lists; the frames are written as PNGs and the README
references them by relative path. Re-running overwrites, so the images are reproducible
from the same console state rather than cropped by hand.
"""
from __future__ import annotations

import argparse
import pathlib

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:4484")
    parser.add_argument("--run", required=True, help="the run id to open")
    parser.add_argument("--round", type=int, default=None,
                        help="the round to open in the editor; default: the last one")
    parser.add_argument("--out", default=".github/assets")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": args.height},
                                device_scale_factor=2)
        # The version page is the run/round picker plus the curve; the editor is a page of
        # its own, addressed by candidate id (`<run>-r<round>`).
        page.goto(f"{args.url}/?mode=harness#/version", wait_until="networkidle")
        page.wait_for_timeout(1800)
        page.screenshot(path=str(out / "console-version.png"))
        rounds = page.eval_on_selector_all("#h-picker [data-round]",
                                           "els => els.map(e => e.dataset.round)")
        round_no = args.round if args.round is not None else (rounds[-1] if rounds else 0)
        page.goto(f"{args.url}/harness.html?candidate={args.run}-r{round_no}",
                  wait_until="networkidle")
        page.wait_for_selector(".hg-srcbar__row[aria-selected='true']", timeout=20000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / "console-editor.png"))
        browser.close()
    print(f"wrote {out / 'console-version.png'} and {out / 'console-editor.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
