#!/usr/bin/env python3
"""Draw the two trend panels the README shows, from the recorded-rounds receipt.

Why the axes are what they are
------------------------------
The figure answers one question: **did the curve move while the method edited the harness?**
It does not answer "how good is this harness", and a number read off it would be quoted
without the three things that make a score comparable -- the harness tree hash, the task set,
and the scorer identity. So the vertical axis carries no ticks. Round 0 is the leftmost point
on every line: the harness as it was handed to the method.

One line per recorded run, one colour per method. A method with two runs therefore appears as
two shapes in the same colour, which is the honest way to show a single sample without
inventing an average.

The receipt (`.github/assets/trends.json`) carries the run ids, the curve hashes and the
harness hash of round 0 for every line, so a reader can go back to the run that drew it. The
runs themselves are never committed (`runs/` is not in git), which is why the receipt exists.

Usage
-----
    python3 tools/render_trends.py            # writes .github/assets/trends.svg
    python3 tools/render_trends.py --check    # exit 1 if the SVG is stale

Matplotlib is optional and only this script needs it; the platform, the runner and every test
are standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / ".github" / "assets" / "trends.json"
FIGURE = ROOT / ".github" / "assets" / "trends.svg"

#: One colour per method, assigned in the receipt's order so the two panels agree: the same
#: method is the same colour in both panels, which is what makes the pair readable side by
#: side. Enough colours for the methods we have; a new method takes the next free slot.
PALETTE = (
    "#2f6f8f", "#c2603a", "#5c8a57", "#8a5a9e", "#b08930",
    "#3f7f74", "#a04a6b", "#4a6fa5", "#7a7a2f", "#8f5c3f",
)

#: Ink colours kept neutral so the series carry the only strong signal.
INK = "#1f2328"
MUTED = "#6e7781"
GRID = "#e6e8eb"


def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def _method_colours(panels: list[dict]) -> dict[str, str]:
    names: list[str] = []
    for panel in panels:
        for run in panel["runs"]:
            if run["label"] not in names:
                names.append(run["label"])
    return {name: PALETTE[index % len(PALETTE)] for index, name in enumerate(sorted(names))}


def render(data: dict) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # A constant salt: matplotlib derives element ids from it and from the process, so
    # without it two renders of the same data differ in the ids and `--check` can never pass.
    matplotlib.rcParams["svg.hashsalt"] = "harnessgrad-trends"
    from matplotlib.lines import Line2D

    panels = data["panels"]
    colours = _method_colours(panels)
    figure, axes = plt.subplots(
        1, len(panels), figsize=(9.6, 3.5), dpi=100,
        gridspec_kw={"wspace": 0.12})
    if len(panels) == 1:
        axes = [axes]

    for axis, panel in zip(axes, panels):
        for run in panel["runs"]:
            scores = run["scores"]
            colour = colours[run["label"]]
            axis.plot(range(len(scores)), scores, color=colour, linewidth=1.35,
                      alpha=0.85, solid_capstyle="round")
            axis.scatter([0], [scores[0]], s=9, color=colour, zorder=3)
        axis.set_xlabel("round", color=MUTED, fontsize=9)
        axis.set_title(panel["title"], color=INK, fontsize=10.5, loc="left", pad=8)
        axis.grid(axis="x", color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            axis.spines[side].set_color(GRID)
        # The point of the figure: a shape, not a value.
        axis.set_yticks([])
        axis.tick_params(axis="x", colors=MUTED, labelsize=8, length=3)
        axis.set_xticks(list(range(0, max(len(run["scores"]) for run in panel["runs"]), 5)))
        low = min(min(run["scores"]) for run in panel["runs"])
        high = max(max(run["scores"]) for run in panel["runs"])
        span = max(high - low, 0.05)
        axis.set_ylim(low - 0.08 * span, high + 0.10 * span)

    axes[0].set_ylabel("score (unlabelled on purpose)", color=MUTED, fontsize=9)

    handles = [Line2D([0], [0], color=colours[name], linewidth=1.6, label=name)
               for name in sorted(colours)]
    figure.legend(handles=handles, loc="lower center", ncol=min(len(handles), 7),
                  frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, 0.005),
                  handlelength=1.8, columnspacing=1.4)
    figure.suptitle(
        "Recorded rounds, one line per method. No held-out score, no axis numbers: "
        "the shape is the claim, not the value.",
        color=MUTED, fontsize=8.5, x=0.012, ha="left", y=0.985)
    figure.subplots_adjust(left=0.06, right=0.985, top=0.84, bottom=0.24)
    return _svg(figure)


def _write_png(data: dict, target: Path) -> None:
    """A raster copy for review; not the committed artifact, because it does not scale."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["svg.hashsalt"] = "harnessgrad-trends"
    figure = plt.gcf()
    if figure.axes:
        plt.close(figure)
    render(data)
    plt.gcf().savefig(target, format="png", dpi=140)
    plt.close("all")


def _svg(figure) -> str:
    """The SVG as text, with the volatile metadata stripped.

    Matplotlib stamps the version and a creation date into the file, which would make
    `--check` fail on every machine and every day. Neither is content.
    """
    import io
    import re

    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", metadata={"Date": None})
    text = buffer.getvalue()
    text = re.sub(r"<!-- Created with matplotlib.*?-->\n?", "", text, flags=re.DOTALL)
    return re.sub(r"<dc:date>.*?</dc:date>", "", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed SVG differs from a fresh render")
    parser.add_argument("--png", default="",
                        help="also write a PNG (for a review or a slide; the SVG is the "
                             "committed artifact)")
    args = parser.parse_args()

    svg = render(receipt())
    if args.png:
        _write_png(receipt(), Path(args.png))
    if args.check:
        committed = FIGURE.read_text(encoding="utf-8") if FIGURE.is_file() else ""
        if committed != svg:
            print(f"stale: {FIGURE.relative_to(ROOT)}; run python3 tools/render_trends.py",
                  file=sys.stderr)
            return 1
        print(f"trends.svg matches the receipt ({len(svg)} bytes, "
              f"sha256 {hashlib.sha256(svg.encode()).hexdigest()[:12]})")
        return 0
    FIGURE.write_text(svg, encoding="utf-8")
    print(f"wrote {FIGURE.relative_to(ROOT)} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
