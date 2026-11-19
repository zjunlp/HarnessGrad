"""SVG renderers shared by the benchmark exporters."""


def svg_chart(data: dict, pool: str, methods: list[dict], *, show_gap_note: bool = True) -> str:
    """A line chart of every method's score by checkpoint round, with the reference line.

    Drawn here rather than in the browser so the chart and the table are the same snapshot:
    a chart library that refetched would let the two disagree, and a reader would have no way
    to know which was wrong.
    """
    width, height = 760, 380
    left, right, top, bottom = 56, 190, 24, 44
    plot_w, plot_h = width - left - right, height - top - bottom
    reference = data["reference"].get(pool)
    # H0 is round 0 of the same series, not an annotation: without it the chart starts at the
    # first improver round and a reader cannot see how much the method added over the harness
    # it was given. It is measured once and shared by every method, so every line starts there.
    rounds = sorted({cell["round"] for method in methods for cell in method["rounds"]
                     if pool in cell["eval"]} | ({0} if reference is not None else set()))
    values = [cell["eval"][pool] for method in methods for cell in method["rounds"]
              if pool in cell["eval"]] + ([reference] if reference is not None else [])
    measured_points = [cell for method in methods for cell in method["rounds"]
                       if pool in cell["eval"]]
    if not measured_points:
        return ""      # a pool with only a reference is a line, not a chart
    lo = min(values + ([reference] if reference is not None else []))
    hi = max(values + ([reference] if reference is not None else []))
    pad = max(0.02, (hi - lo) * 0.1)
    lo, hi = max(0.0, lo - pad), min(1.0, hi + pad)
    span = (hi - lo) or 1.0

    def x(round_no: int) -> float:
        position = rounds.index(round_no)
        return left + (plot_w * position / max(1, len(rounds) - 1))

    def y(value: float) -> float:
        return top + plot_h * (1 - (value - lo) / span)

    colours = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Score by checkpoint round on {pool}">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for step in range(5):
        value = lo + span * step / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" '
                     f'stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" font-size="11" fill="#6b7280" '
                     f'text-anchor="end">{value:.2f}</text>')
    for round_no in rounds:
        parts.append(f'<text x="{x(round_no):.1f}" y="{top + plot_h + 18}" font-size="11" '
                     f'fill="#6b7280" text-anchor="middle">r{round_no}</text>')
    if reference is not None:
        yy = y(reference)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" '
                     f'stroke="#111827" stroke-width="1.2" stroke-dasharray="5 4"/>')
        parts.append(f'<text x="{left + plot_w - 4}" y="{yy - 5:.1f}" font-size="11" '
                     f'fill="#111827" text-anchor="end">H0 (round 0) {reference:.3f}</text>')
    any_gap = False
    for index, method in enumerate(methods):
        points = sorted((cell["round"], cell["eval"][pool]) for cell in method["rounds"]
                        if pool in cell["eval"])
        if reference is not None and points:
            points = [(0, reference)] + points
        if not points:
            continue
        colour = colours[index % len(colours)]
        # **A line must not run through rounds that were never measured.** The first version
        # joined every measured point into one polyline, so `meta_harness` -- whose only
        # `valid_unseen` measurements are round 0 (the shared H0) and round 8 (its exam) --
        # was drawn as a straight line across eight unmeasured rounds, which reads as "it was
        # flat and then jumped". Measured on the published page 2026-10-19 (reported by the
        # operator). A new subpath starts wherever the round index skips.
        subpaths: list[str] = []
        current: list[str] = []
        previous_round = None
        for r, v in points:
            if previous_round is not None and r - previous_round > 1:
                subpaths.append(" ".join(current))
                current = []
            current.append(f"{'M' if not current else 'L'}{x(r):.1f},{y(v):.1f}")
            previous_round = r
        if current:
            subpaths.append(" ".join(current))
        any_gap = any_gap or any(b[0] - a[0] > 1 for a, b in zip(points, points[1:]))
        # One group per method so the page filter can hide a line together with its legend.
        parts.append(f'<g data-method-line="{method["name"]}">')
        for subpath in subpaths:
            parts.append(f'<path d="{subpath}" fill="none" stroke="{colour}" stroke-width="2"/>')
        inherited = {cell["round"]: cell["inherited"] for cell in method["rounds"]
                     if cell.get("inherited")}
        for r, v in points:
            if r in inherited:
                parent = inherited[r]["parentRound"]
                source = inherited[r]["sourceRound"]
                label = f'r{r}: Harness failed, inherits r{parent} (score from r{source})'
                parts.append(f'<rect x="{x(r) - 3.5:.1f}" y="{y(v) - 3.5:.1f}" width="7" height="7" '
                             f'fill="#fff" stroke="{colour}" stroke-width="1.5" '
                             f'data-inherited-round="{r}"><title>{label}</title></rect>')
                continue
            parts.append(f'<circle cx="{x(r):.1f}" cy="{y(v):.1f}" r="2.8" fill="{colour}"/>')
        legend_y = top + 14 + index * 18
        parts.append(f'<line x1="{left + plot_w + 16}" y1="{legend_y}" '
                     f'x2="{left + plot_w + 38}" y2="{legend_y}" stroke="{colour}" '
                     f'stroke-width="2"/>')
        parts.append(f'<text x="{left + plot_w + 44}" y="{legend_y + 4}" font-size="11" '
                     f'fill="#111827">{method["name"]}</text>')
        parts.append("</g>")
    parts.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 6}" font-size="11" '
                 f'fill="#374151" text-anchor="middle">checkpoint round</text>')
    if any_gap and show_gap_note:
        # Only when the data has one: a caption about breaks on a gapless chart is noise.
        parts.append(f'<text x="{left}" y="{height - 22}" font-size="10" fill="#6b7280">'
                     f'a break means that round was not measured</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def svg_train_chart(data: dict, methods: list[dict], *, show_gap_note: bool = True) -> str:
    """The study-side curve: score per round on the 100 studied tasks, starting at H0."""
    reference = (data["reference"] or {}).get("train")
    rows_ = [(cell["round"], cell["train"]) for method in methods for cell in method["rounds"]
             if cell.get("train") is not None]
    if not rows_:
        return ""
    shaped = [{"name": method["name"],
               "rounds": [{**c} for c in method["rounds"]
                          if c.get("train") is not None]} for method in methods
              if any(c.get("train") is not None for c in method["rounds"])]
    # Reuse the eval chart by presenting the study score under a synthetic pool key.
    for method in shaped:
        for cell in method["rounds"]:
            cell["eval"] = {"train": cell["train"]}
    return svg_chart({"reference": {"train": reference}}, "train", shaped, show_gap_note=show_gap_note)


def svg_bar_chart(rows: list[dict], pool: str | None, title: str, note: str = "") -> str:
    """One bar per method, sorted, for a pool that is measured once (not per round).

    Why a bar chart here and not the line chart used for train/dev: the eval side is *one* point
    per method, so a line would be seven isolated dots joined by a line nobody measured. A bar
    comparison is the honest shape for "which method, on material none of them studied".

    `pool=None` means the combined number; otherwise the bar is that pool's score alone.
    """
    cells = []
    for row in rows:
        if pool is None:
            value = row.get("score")
        else:
            value = ((row.get("by_split") or {}).get(pool) or {}).get("score")
        if isinstance(value, (int, float)):
            cells.append((row["method"], float(value)))
    if not cells:
        return ""
    cells.sort(key=lambda kv: (-kv[1], kv[0]))
    width, height = 760, 380
    left, right, top, bottom = 64, 24, 28, 64
    plot_w, plot_h = width - left - right, height - top - bottom
    hi = max(0.05, max(v for _, v in cells))
    hi = min(1.0, hi * 1.15)

    def y(value: float) -> float:
        return top + plot_h * (1 - value / hi)

    colours = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b"]
    slot = plot_w / max(1, len(cells))
    bar_w = min(64.0, slot * 0.55)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for step in range(5):
        value = hi * step / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" '
                     f'stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" font-size="11" fill="#6b7280" '
                     f'text-anchor="end">{value:.2f}</text>')
    for index, (method, value) in enumerate(cells):
        cx = left + slot * (index + 0.5)
        yy = y(value)
        colour = colours[index % len(colours)]
        # `data-method-bar` so the page's method filter hides the bar with the row, exactly like
        # `data-method-line` does for the per-round charts.
        parts.append(f'<g data-method-bar="{method}">')
        parts.append(f'<rect x="{cx - bar_w / 2:.1f}" y="{yy:.1f}" width="{bar_w:.1f}" '
                     f'height="{top + plot_h - yy:.1f}" fill="{colour}" rx="2"/>')
        parts.append(f'<text x="{cx:.1f}" y="{yy - 6:.1f}" font-size="11" fill="#111827" '
                     f'text-anchor="middle">{value:.3f}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{top + plot_h + 18}" font-size="11" fill="#374151" '
                     f'text-anchor="middle">{method}</text>')
        parts.append("</g>")
    if note:
        parts.append(f'<text x="{left}" y="{height - 8}" font-size="10" fill="#6b7280">{note}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"
