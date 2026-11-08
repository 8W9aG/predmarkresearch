"""Build the static website: every registered strategy and its paper performance over time.

Inputs are the repo (strategy registry, experiment reports) and a state directory of
`<strategy>.runs.jsonl` files, which the Modal job writes to the `predmark-paper` volume.
Output is plain HTML, CSS, and one small script, with no external requests.
"""

from __future__ import annotations

import html
import json
import math
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import strategies

STATIC = Path(__file__).parent / "static"
VOLUME = "predmark-paper"


# --- inputs -------------------------------------------------------------------------------


def fetch_from_modal(dest: str | Path, volume: str = VOLUME) -> list[str]:
    """Copy every `*.runs.jsonl` from the Modal volume into `dest`. Returns the files copied."""
    import modal

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    vol = modal.Volume.from_name(volume)
    copied = []
    for entry in vol.listdir("/"):
        name = entry.path.lstrip("/")
        if name.endswith(".runs.jsonl"):
            (dest / name).write_bytes(b"".join(vol.read_file(name)))
            copied.append(name)
    return copied


def load_runs(state_dir: str | Path, name: str) -> list[dict[str, Any]]:
    path = Path(state_dir) / f"{name}.runs.jsonl"
    if not path.exists():
        return []
    runs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return sorted(runs, key=lambda r: r["ts"])


def repo_url(root: Path) -> str | None:
    """https URL of the origin remote, for linking to reports, or None."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.match(r"(?:git@|https://)([^:/]+)[:/](.+?)(?:\.git)?$", url)
    return f"https://{match[1]}/{match[2]}" if match else None


def evidence_summary(root: Path, directory: str) -> dict[str, str]:
    """Title and the leading result paragraph of an experiment's report.md."""
    report = root / directory / "report.md"
    if not report.exists():
        return {"title": directory, "summary": "No report yet.", "path": directory}
    lines = report.read_text().splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), directory)
    paragraphs = "\n".join(lines).split("\n\n")
    body = next((p for p in paragraphs[1:] if p.strip() and not p.startswith("#")), "")
    plain = re.sub(r"[*`]", "", " ".join(body.split()))
    return {"title": title, "summary": plain, "path": f"{directory}/report.md"}


# --- formatting ---------------------------------------------------------------------------


def money(value: float, signed: bool = False) -> str:
    sign = "−" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def count(value: float) -> str:
    return f"{int(value):,}"


def day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%b %-d, %Y")


def stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


def series(runs: list[dict[str, Any]]) -> dict[str, list[float]]:
    acct = [r["account"] for r in runs]
    return {
        "ts": [r["ts"] for r in runs],
        "marked": [float(a.get("mark_nav") or a["realized_pnl"]) for a in acct],
        "realized": [float(a["realized_pnl"]) for a in acct],
        "risk": [float(a["capital_at_risk"]) for a in acct],
        "open": [float(a["open_positions"]) for a in acct],
        "settled": [float(a["settled_positions"]) for a in acct],
        "fees": [float(a["fees_paid"]) for a in acct],
    }


# --- charts -------------------------------------------------------------------------------

W, H = 720, 260
PAD = {"left": 64, "right": 96, "top": 16, "bottom": 32}


def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 10))
        t += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def line_chart(
    chart_id: str,
    ts: list[float],
    lines: list[dict[str, Any]],
    fmt: str = "money",
    zero_line: bool = True,
) -> str:
    """SVG line chart on one y-axis. `lines`: [{"key", "label", "values", "slot"}]."""
    values = [v for line in lines for v in line["values"]]
    lo, hi = min(values + ([0.0] if zero_line else [])), max(values + ([0.0] if zero_line else []))
    ticks = nice_ticks(lo, hi)
    y_lo, y_hi = ticks[0], ticks[-1]
    t_lo, t_hi = (ts[0], ts[-1]) if ts[0] != ts[-1] else (ts[0] - 43200, ts[0] + 43200)
    x0, x1 = PAD["left"], W - PAD["right"]
    y0, y1 = H - PAD["bottom"], PAD["top"]

    def x(t: float) -> float:
        return x0 + (t - t_lo) / (t_hi - t_lo) * (x1 - x0)

    def y(v: float) -> float:
        return y0 - (v - y_lo) / (y_hi - y_lo) * (y0 - y1)

    label = money if fmt == "money" else count
    parts = [
        f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
        f'aria-labelledby="{chart_id}-title" data-chart="{chart_id}">'
    ]
    for t in ticks:
        cls = "baseline" if t == 0 and zero_line else "grid"
        parts.append(f'<line class="{cls}" x1="{x0}" x2="{x1}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(
            f'<text class="tick" x="{x0 - 8}" y="{y(t) + 4:.1f}" text-anchor="end">'
            f"{e(label(t))}</text>"
        )
    for t in sorted({ts[0], ts[-1]}):
        anchor = "start" if t == ts[0] and len(set(ts)) > 1 else "middle"
        if t == ts[-1] and len(set(ts)) > 1:
            anchor = "end"
        parts.append(
            f'<text class="tick" x="{x(t):.1f}" y="{H - 10}" text-anchor="{anchor}">'
            f"{e(day(t))}</text>"
        )
    ends = []
    for line in lines:
        pts = [(x(t), y(v)) for t, v in zip(ts, line["values"], strict=True)]
        if len(pts) > 1:
            d = " ".join(
                f"{'M' if i == 0 else 'L'}{px:.1f},{py:.1f}" for i, (px, py) in enumerate(pts)
            )
            parts.append(f'<path class="line s{line["slot"]}" d="{d}"/>')
        ex, ey = pts[-1]
        parts.append(f'<circle class="dot s{line["slot"]}" cx="{ex:.1f}" cy="{ey:.1f}" r="4"/>')
        ends.append([ey, line])
    ends.sort(key=lambda item: item[0])
    for i in range(1, len(ends)):  # keep end labels at least 14px apart
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 14)
    for ey, line in ends:
        parts.append(
            f'<text class="endlabel" x="{x1 + 10}" y="{ey + 4:.1f}">'
            f"{e(label(line['values'][-1]))}</text>"
        )
    parts.append(
        f'<line class="crosshair" x1="0" x2="0" y1="{y1}" y2="{y0}" visibility="hidden"/>'
        f'<rect class="hit" x="{x0}" y="{y1}" width="{x1 - x0}" height="{y0 - y1}"/>'
        "</svg>"
    )
    data = {
        "x": [round(x(t), 1) for t in ts],
        "dates": [stamp(t) for t in ts],
        "series": [
            {"label": ln["label"], "slot": ln["slot"], "text": [label(v) for v in ln["values"]]}
            for ln in lines
        ],
        "width": W,
    }
    payload = json.dumps(data).replace("</", "<\\/")
    legend = ""
    if len(lines) > 1:
        keys = "".join(
            f'<span class="key"><span class="swatch s{ln["slot"]}"></span>{e(ln["label"])}</span>'
            for ln in lines
        )
        legend = f'<div class="legend">{keys}</div>'
    return (
        f'<div class="chart-wrap">{legend}{"".join(parts)}'
        f'<div class="tooltip" hidden></div>'
        f'<script type="application/json" id="{chart_id}-data">{payload}</script></div>'
    )


# --- pages --------------------------------------------------------------------------------


def page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<main>
{body}
</main>
<script src="{up}chart.js" defer></script>
</body>
</html>
"""


NOTICE = (
    '<p class="notice"><strong>Paper trading only.</strong> Fills are simulated against live '
    "Kalshi order books. No real money is placed.</p>"
)


def tiles(s: dict[str, list[float]]) -> str:
    items = [
        ("P&L, marked to market", money(s["marked"][-1], signed=True)),
        ("Realized P&L", money(s["realized"][-1], signed=True)),
        ("Capital at risk", money(s["risk"][-1])),
        ("Open positions", count(s["open"][-1])),
        ("Settled positions", count(s["settled"][-1])),
        ("Fees paid", money(s["fees"][-1])),
    ]
    cells = "".join(
        f'<div class="tile"><div class="tile-label">{e(k)}</div>'
        f'<div class="tile-value">{e(v)}</div></div>'
        for k, v in items
    )
    return f'<div class="tiles">{cells}</div>'


def runs_table(runs: list[dict[str, Any]]) -> str:
    head = (
        "<tr><th>Run</th><th>Entered</th><th>Settled</th><th>Open</th>"
        "<th>Marked P&amp;L</th><th>Realized P&amp;L</th><th>Capital at risk</th>"
        "<th>Fees</th></tr>"
    )
    rows = []
    for r in reversed(runs):
        a, p = r["account"], r["pass"]
        rows.append(
            f"<tr><td>{e(stamp(r['ts']))}</td><td>{count(p.get('entered', 0))}</td>"
            f"<td>{count(p.get('settled', 0))}</td><td>{count(a['open_positions'])}</td>"
            f"<td>{e(money(float(a.get('mark_nav') or a['realized_pnl']), True))}</td>"
            f"<td>{e(money(float(a['realized_pnl']), True))}</td>"
            f"<td>{e(money(float(a['capital_at_risk'])))}</td>"
            f"<td>{e(money(float(a['fees_paid'])))}</td></tr>"
        )
    return f'<div class="table-wrap"><table>{head}{"".join(rows)}</table></div>'


def evidence_html(root: Path, strategy: Any, base_url: str | None) -> str:
    if not strategy.evidence:
        return "<p>No experiment has tested this strategy yet.</p>"
    items = []
    for directory in strategy.evidence:
        ev = evidence_summary(root, directory)
        link = (
            f'<a href="{e(base_url)}/blob/main/{e(ev["path"])}">{e(ev["path"])}</a>'
            if base_url
            else f"<code>{e(ev['path'])}</code>"
        )
        items.append(
            f'<article class="evidence"><h3>{e(ev["title"])}</h3>'
            f'<p>{e(ev["summary"])}</p><p class="muted">{link}</p></article>'
        )
    return "".join(items)


def strategy_page(root: Path, strategy: Any, runs: list[dict[str, Any]], base: str | None) -> str:
    body = [
        '<p><a href="../index.html">← All strategies</a></p>',
        f"<h1>{e(strategy.name)}</h1>",
        f'<p class="lede">{e(strategy.description)}</p>',
        NOTICE,
    ]
    if not runs:
        body.append("<p>No paper runs yet.</p>")
    else:
        s = series(runs)
        last = runs[-1]
        body += [
            f'<p class="muted">{len(runs)} runs, {e(day(runs[0]["ts"]))} to '
            f"{e(day(last['ts']))}. Last run {e(stamp(last['ts']))}.</p>",
            tiles(s),
            '<section><h2 id="pnl-title">P&amp;L over time</h2>',
            '<p class="muted">Marked P&amp;L values open positions at the listing bid or ask. '
            "Positions without a quote are marked at their worst case.</p>",
            line_chart(
                "pnl",
                s["ts"],
                [
                    {"label": "Marked P&L", "values": s["marked"], "slot": 1},
                    {"label": "Realized P&L", "values": s["realized"], "slot": 2},
                ],
            ),
            "</section>",
            '<section><h2 id="risk-title">Capital at risk</h2>',
            '<p class="muted">The most the open positions could lose if every one went against '
            "the strategy.</p>",
            line_chart(
                "risk", s["ts"], [{"label": "Capital at risk", "values": s["risk"], "slot": 1}]
            ),
            "</section>",
            "<section><h2>Every run</h2>",
            runs_table(runs),
            "</section>",
            "<section><h2>Latest run</h2>",
            f"<pre>{e(json.dumps({k: last[k] for k in last if k != 'account'}, indent=2))}</pre>",
            "</section>",
        ]
    body += ["<section><h2>Research</h2>", evidence_html(root, strategy, base), "</section>"]
    return page(f"{strategy.name}: predmark", "\n".join(body), depth=1)


def index_page(cards: list[str], built: float) -> str:
    body = [
        "<h1>predmark strategies</h1>",
        '<p class="lede">Strategies this system runs on Kalshi, and how each has done over '
        "time.</p>",
        NOTICE,
        '<div class="cards">',
        *cards,
        "</div>",
        f'<p class="muted">Built {e(stamp(built))} from the repo and the paper journals.</p>',
    ]
    return page("predmark strategies", "\n".join(body))


def card(strategy: Any, runs: list[dict[str, Any]]) -> str:
    href = f"strategies/{e(strategy.name)}.html"
    if runs:
        s = series(runs)
        stats = (
            f'<div class="card-stats"><div><div class="tile-label">Marked P&amp;L</div>'
            f'<div class="tile-value">{e(money(s["marked"][-1], True))}</div></div>'
            f'<div><div class="tile-label">Open positions</div>'
            f'<div class="tile-value">{e(count(s["open"][-1]))}</div></div>'
            f'<div><div class="tile-label">Runs</div>'
            f'<div class="tile-value">{len(runs)}</div></div></div>'
            f'<p class="muted">Last run {e(stamp(runs[-1]["ts"]))}</p>'
        )
    else:
        stats = '<p class="muted">No paper runs yet.</p>'
    return (
        f'<a class="card" href="{href}"><h2>{e(strategy.name)}</h2>'
        f"<p>{e(strategy.description)}</p>{stats}</a>"
    )


def build(
    out: str | Path = "site",
    state_dir: str | Path = "data/paper",
    root: str | Path = ".",
    built: float | None = None,
) -> Path:
    """Write the site to `out`, replacing what was there. Returns `out`."""
    out, root = Path(out), Path(root)
    built = built if built is not None else datetime.now(UTC).timestamp()
    if out.exists():
        shutil.rmtree(out)
    (out / "strategies").mkdir(parents=True)
    for asset in STATIC.iterdir():
        shutil.copy(asset, out / asset.name)
    base = repo_url(root)
    cards = []
    for strategy in strategies.registry().values():
        runs = load_runs(state_dir, strategy.name)
        (out / "strategies" / f"{strategy.name}.html").write_text(
            strategy_page(root, strategy, runs, base)
        )
        cards.append(card(strategy, runs))
    (out / "index.html").write_text(index_page(cards, built))
    return out
