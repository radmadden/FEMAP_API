"""PDF report (reportlab) with matplotlib charts, plus results.json.

Layout (landscape letter, so the wide 6-DOF tables fit):
  page 1   model, date, Nastran version, units, mount; the PASS/FAIL table
  then     one section per check: verdict, criteria/notes, tables, chart
  appendix all FATAL/WARNING messages, run times, case control carried over

Colors follow the corporate palette. Primary: black #191919, light green #37d233,
white #ffffff; secondary: dark green #00890b, light gray #d5d8cb. Only these text /
background combinations are used:
  dark green on white      white on dark green      light green on black
  black on light green     light gray on black      black on light gray
  white on black           black on white
Every shaded table cell therefore sets its text color with its background (COMBO).
Chart series are dark green and light green (validated as distinguishable for color-vision
deficiency); black is used for reference lines, light gray for gridlines. Light green is
below 3:1 contrast on white, so every chart's numbers are also in a table beside it.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

import numpy as np

BLACK, LIGHT_GREEN, WHITE = "#191919", "#37d233", "#ffffff"   # primary
DARK_GREEN, LIGHT_GRAY = "#00890b", "#d5d8cb"                  # secondary

# (background, text) pairs: the only combinations used anywhere in the report.
COMBO = {
    "body": (WHITE, BLACK),            # black on white
    "accent": (WHITE, DARK_GREEN),     # dark green on white (headings, PASS text)
    "header": (DARK_GREEN, WHITE),     # white on dark green (table header rows)
    "pair": (LIGHT_GREEN, BLACK),      # black on light green: values compared with each other
    "group_a": (LIGHT_GRAY, BLACK),    # black on light gray: a row group (e.g. rigid-body modes)
    "group_b": (DARK_GREEN, WHITE),    # white on dark green: a second row group (first elastic mode)
    "fail": (BLACK, WHITE),            # white on black: a value beyond its limit
    "box": (LIGHT_GRAY, BLACK),        # black on light gray: verbatim .f06 blocks
}
# Verdict badges: (background, text)
STATUS_COMBO = {"PASS": (DARK_GREEN, WHITE), "FAIL": (BLACK, WHITE), "ERROR": (BLACK, WHITE),
                "INFO": (LIGHT_GRAY, BLACK), "SKIPPED": (LIGHT_GRAY, BLACK)}

SERIES = [DARK_GREEN, LIGHT_GREEN, BLACK]   # BLACK only with markers/direct labels
INK = INK2 = MUTED = BLACK
GRID, SURFACE = LIGHT_GRAY, WHITE
HIGHLIGHT = {k: COMBO[k][0] for k in ("pair", "group_a", "group_b", "fail")}  # backward-compatible


# ------------------------------------------------------------------ JSON
def _plain(o):
    if is_dataclass(o):
        return {k: _plain(v) for k, v in asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
        return None
    return o


def write_json(path: Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(_plain(payload), indent=1))


# ------------------------------------------------------------------ charts
def _axes(fig_w=7.0, fig_h=3.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    return fig, ax, plt


def _save(fig, plt, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_freefree(data: dict, path: Path, ratio_limit: float) -> Path:
    hz = np.array(data["freq_hz"], dtype=float)
    fig, ax, plt = _axes()
    x = np.arange(1, len(hz) + 1)
    floor = max(hz[hz > 0].min() / 10 if (hz > 0).any() else 1e-6, 1e-12)
    colors = [DARK_GREEN if i < 6 else LIGHT_GREEN for i in range(len(hz))]
    ax.bar(x, np.maximum(hz, floor) - floor, bottom=floor, width=0.6, color=colors,
           edgecolor=SURFACE, linewidth=1.5)
    ax.set_yscale("log")
    lim = data["f7"] * ratio_limit
    ax.axhline(lim, color=INK2, linewidth=1, linestyle="--")
    ax.text(len(hz) + 0.4, lim, f" ratio limit\n f7 x {ratio_limit:.0e}", va="center",
            fontsize=7, color=INK2)
    ax.set_xticks(x)
    ax.set_xlabel("Mode", color=INK2, fontsize=8)
    ax.set_ylabel("Frequency (Hz, log)", color=INK2, fontsize=8)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=SERIES[0], label="rigid-body (1-6)"),
                       Patch(color=SERIES[1], label="elastic")],
              fontsize=7, frameon=False, loc="upper left")
    return _save(fig, plt, path)


def chart_constrained(data: dict, path: Path) -> Path:
    fig, ax, plt = _axes()
    n = len(data["freq_hz"])
    x = np.arange(1, n + 1)
    for i, d in enumerate(["T1", "T2", "T3"]):
        frac = np.array(data["fractions"].get(d, [0.0] * n), dtype=float)
        cum = np.cumsum(frac)
        # Distinct markers too, so the series never rely on color alone (black has no hue).
        ax.plot(x, cum, color=SERIES[i], linewidth=2, marker="osD"[i], markersize=4, label=d)
        ax.annotate(f"{d} {cum[-1]:.2f}", (x[-1], cum[-1]), xytext=(6, 0),
                    textcoords="offset points", fontsize=7, color=INK2, va="center")
    ax.set_ylim(0, 1.05)
    ax.set_xticks(x)
    ax.set_xlabel("Mode", color=INK2, fontsize=8)
    ax.set_ylabel("Cumulative effective-mass fraction", color=INK2, fontsize=8)
    ax.legend(fontsize=7, frameon=False, loc="lower right")
    return _save(fig, plt, path)


def chart_thermal(data: dict, path: Path, limit: float) -> Path:
    fig, ax, plt = _axes()
    dist = np.array(data["dist"], dtype=float)
    err = np.array(data["err"], dtype=float)
    if len(dist) > 20000:  # keep the PDF light; the worst points are always kept
        keep = np.argsort(err)[-2000:]
        rest = np.random.default_rng(0).choice(len(dist), 18000, replace=False)
        idx = np.union1d(keep, rest)
        dist, err = dist[idx], err[idx]
    ax.scatter(dist, err, s=8, color=SERIES[0], edgecolors="none")
    ax.axhline(limit, color=INK2, linewidth=1, linestyle="--")
    ax.text(dist.max() if len(dist) else 1, limit, f" limit {limit:.0e}", va="bottom",
            ha="right", fontsize=7, color=INK2)
    ax.set_xlabel("Distance from mount grid", color=INK2, fontsize=8)
    ax.set_ylabel("|u - u_theory| / max |u_theory|", color=INK2, fontsize=8)
    return _save(fig, plt, path)


def chart_quality(quality: dict, path: Path) -> Path | None:
    metrics = [m for m in ("max_aspect_ratio", "max_skew_angle", "max_warp_angle",
                           "min_interior_angle") if m in quality and quality[m].get("values")]
    if not metrics:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, len(metrics), figsize=(2.4 * len(metrics), 2.4), dpi=150)
    axs = np.atleast_1d(axs)
    fig.patch.set_facecolor(SURFACE)
    for ax, m in zip(axs, metrics):
        ax.set_facecolor(SURFACE)
        ax.hist(quality[m]["values"], bins=30, color=SERIES[0], edgecolor=SURFACE, linewidth=0.8)
        ax.set_title(m.replace("_", " "), fontsize=8, color=INK)
        ax.tick_params(colors=INK2, labelsize=7)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.grid(True, axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    return _save(fig, plt, path)


# ------------------------------------------------------------------ PDF
def build_pdf(path: Path, header: dict, check_results: list, charts: dict,
              messages: list, runs: list, method: list | None = None,
              model_file: str = "") -> Path:
    """method: [(heading, [paragraphs])] from method.method_sections(); printed as
    Appendix A so the report explains its own criteria and their reasoning.
    model_file: the original model deck's file name; shown in the title and in the footer
    of every page, so a printed page always identifies its model."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)
    from xml.sax.saxutils import escape

    ss = getSampleStyleSheet()
    black, white = colors.HexColor(BLACK), colors.HexColor(WHITE)
    dkgreen, ltgray = colors.HexColor(DARK_GREEN), colors.HexColor(LIGHT_GRAY)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=8.5, leading=11, textColor=black)
    small = ParagraphStyle("small", parent=body, fontSize=7, leading=8.5)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=6, textColor=dkgreen)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=4, spaceAfter=4,
                        textColor=dkgreen)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=9.5, spaceBefore=6, spaceAfter=2,
                        textColor=black)
    _cell_styles: dict = {}

    def cell_style(fg: str, bold: bool = False):
        """'small' in a given text color (cached); cells are Paragraphs, so the text color
        must be on the paragraph, not on the table style."""
        k = (fg, bold)
        if k not in _cell_styles:
            _cell_styles[k] = ParagraphStyle(f"cell{len(_cell_styles)}", parent=small,
                                             textColor=colors.HexColor(fg),
                                             fontName="Helvetica-Bold" if bold else "Helvetica")
        return _cell_styles[k]

    def badge(status: str) -> str:
        """Verdict as a colored badge in paragraph markup, from STATUS_COMBO."""
        bg, fg = STATUS_COMBO.get(status, COMBO["body"])
        return f"<font color='{fg}' backColor='{bg}'>&nbsp;{status}&nbsp;</font>"

    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter), leftMargin=0.5 * inch,
                            rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title=f"FEM validation checks: {model_file}" if model_file
                            else "FEM validation checks", author="fem_checks")
    width = landscape(letter)[0] - inch
    page_w = landscape(letter)[0]
    stamp = f"{_dt.datetime.now():%Y-%m-%d %H:%M}"

    def footer(canvas, doc_):
        """Model file name, run date and page number at the foot of every page."""
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(black)
        canvas.drawString(0.5 * inch, 0.3 * inch,
                          f"FEM validation checks  |  {model_file or '-'}  |  {stamp}")
        canvas.drawRightString(page_w - 0.5 * inch, 0.3 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=6.6, leading=7.6)

    def excerpt(title, lines):
        """Verbatim .f06 text in a light box, split across pages if long."""
        from reportlab.platypus import Preformatted
        head = Paragraph(escape(f"Nastran output: {title} (verbatim from the .f06)"), h3)
        out = []
        chunk = 60  # lines per box (60 x 7.6 pt fits the 528 pt frame), so long blocks break between pages
        for i in range(0, len(lines), chunk):
            p = Preformatted("\n".join(x[:150] for x in lines[i:i + chunk]), mono)
            t = Table([[p]], colWidths=[width - 14])
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(COMBO["box"][0])),
                                   ("BOX", (0, 0), (-1, -1), 0.3, black),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
            out.append(t)
        if out:  # keep the heading on the same page as the first box
            out[0] = KeepTogether([head, out[0]])
        return out

    def table(headers, rows, col_widths=None, status_col=None, highlight=()):
        """A data table. Header row white on dark green; body black on white; highlighted
        cells and the verdict column use the COMBO / STATUS_COMBO pairs, so every cell's
        text color always matches its background."""
        nr, nc = len(rows), len(headers)
        cell = {}  # (row, col) -> (bg, fg, bold), data rows only
        for (r, c0, c1, key) in highlight or ():
            if key in COMBO and 0 <= r < nr:
                bg, fg = COMBO[key]
                for c in range(max(c0, 0), min(c1, nc - 1) + 1):
                    cell[(r, c)] = (bg, fg, key in ("pair", "fail"))
        if status_col is not None:
            for i, r in enumerate(rows):
                s_ = str(r[status_col]).upper()
                key = "PASS" if s_ in ("OK", "PASS") else s_
                if key in STATUS_COMBO:
                    bg, fg = STATUS_COMBO[key]
                    cell[(i, status_col)] = (bg, fg, True)
        hbg, hfg = COMBO["header"]
        data = [[Paragraph(f"<b>{escape(str(h))}</b>", cell_style(hfg)) for h in headers]]
        for i, r in enumerate(rows):
            data.append([Paragraph(escape(str(v)), cell_style(cell.get((i, j), (None, BLACK, False))[1],
                                                               cell.get((i, j), (None, None, False))[2]))
                         for j, v in enumerate(r)])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        style = [("GRID", (0, 0), (-1, -1), 0.3, ltgray),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(hbg)),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]
        for (i, j), (bg, _fg, _b) in cell.items():
            style.append(("BACKGROUND", (j, i + 1), (j, i + 1), colors.HexColor(bg)))
        t.setStyle(TableStyle(style))
        return t

    story = [Paragraph("FEM validation checks" + (f": {escape(model_file)}" if model_file else ""), h1)]
    meta = [[k, v] for k, v in header.items()]
    story.append(table(["Item", "Value"], meta, [2.2 * inch, width - 2.2 * inch]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Results", h2))
    summary_rows = [[c.title, c.status, c.summary] for c in check_results]
    story.append(table(["Check", "Result", "Key numbers / reason"], summary_rows,
                       [2.6 * inch, 0.8 * inch, width - 3.4 * inch], status_col=1))
    story.append(Paragraph(
        "PASS/FAIL criteria follow FEMCI 'The Book' validity checks (femci ~1880) with the "
        "design decisions in fem_checks/docs/design.md. INFO checks are report-only.", small))

    for c in check_results:
        story.append(PageBreak())
        story.append(Paragraph(f"{escape(c.title)}: {badge(c.status)}", h2))
        story.append(Paragraph(escape(c.summary), body))
        for d in c.details:
            story.append(Paragraph("&bull; " + escape(str(d)), body))
        img = charts.get(c.key)
        if img and Path(img).is_file():
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(str(img)).getSize()
            w = min(width, 7.5 * inch)
            story.append(Spacer(1, 4))
            story.append(Image(str(img), width=w, height=w * ih / iw))
        for t in c.tables:
            if not t.rows:
                continue
            status_col = len(t.headers) - 1 if str(t.headers[-1]).lower() == "result" else None
            title_p = Paragraph(escape(t.title), h3)
            tbl = table(t.headers, t.rows, status_col=status_col,
                        highlight=getattr(t, "highlight", ()))
            # Short tables stay on one page with their title; long ones may split
            # (the header row repeats on each page).
            if len(t.rows) < 25:
                story.append(KeepTogether([title_p, tbl]))
            else:
                story += [title_p, tbl]
            if t.note:
                story.append(Paragraph(escape(t.note), small))
        for title, lines in getattr(c, "excerpts", []) or []:
            if lines:
                story += excerpt(title, lines)

    if method:
        story.append(PageBreak())
        story.append(Paragraph("Appendix A: Method", h2))
        story.append(Paragraph(
            "What each check runs, its pass criteria with the values applied in this run, and "
            "the reasoning behind them. Dn refers to the design decisions in "
            "fem_checks/docs/design.md.", small))
        for heading, paras in method:
            story.append(Paragraph(escape(heading), h3))
            for p in paras:
                story.append(Paragraph(escape(p), body))
                story.append(Spacer(1, 3))
    story.append(PageBreak())
    story.append(Paragraph("Appendix B: Nastran runs and messages", h2))
    story.append(table(["Deck", "Seconds", "Return code", "Status"],
                       [[Path(r.deck).name, f"{r.seconds:.1f}", r.returncode, r.error or "ok"] for r in runs]))
    if messages:
        story.append(Paragraph("FATAL / WARNING messages", h3))
        story.append(table(["Deck", "Kind", "Number", "Text"],
                           [[d, m.kind, m.number, m.text[:300]] for d, m in messages],
                           [1.6 * inch, 0.7 * inch, 0.7 * inch, width - 3.0 * inch]))
    else:
        story.append(Paragraph("No FATAL or WARNING messages.", body))
    story.append(Paragraph(f"Generated {_dt.datetime.now():%Y-%m-%d %H:%M} by fem_checks. "
                           "Untested in production until the first-run checklist in "
                           "fem_checks/docs/design.md is complete.", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return Path(path)
