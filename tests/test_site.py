"""The static site builds from run logs, escapes untrusted text, and handles no runs."""

import json

from predmark.site import build as site


def run(ts, marked, realized=0.0, risk=600.0, open_=800, settled=0):
    return {
        "ts": ts,
        "account": {
            "mark_nav": str(marked),
            "realized_pnl": str(realized),
            "capital_at_risk": str(risk),
            "open_positions": open_,
            "settled_positions": settled,
            "fees_paid": "12.00",
        },
        "pass": {"entered": 10, "settled": settled},
        "seconds": 120,
    }


def write_runs(path, runs):
    path.write_text("\n".join(json.dumps(r) for r in runs) + "\n")


def test_build_writes_index_and_strategy_pages(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    write_runs(
        state / "nothing-ever-happens.runs.jsonl",
        [run(1794268800 + d * 86400, -30 - d, realized=-d, settled=d * 50) for d in range(5)],
    )
    out = site.build(tmp_path / "site", state, root=".", built=1794268800)
    index = (out / "index.html").read_text()
    page = (out / "strategies" / "nothing-ever-happens.html").read_text()
    assert "Paper trading only" in index and "nothing-ever-happens" in index
    assert "−$34.00" in page  # latest marked P&L, as a tile and in the table
    assert page.count("<tr>") == 6  # header plus 5 runs
    assert "E002 report" in page  # research evidence from the repo
    assert (out / "style.css").exists() and (out / "chart.js").exists()


def test_build_with_no_runs(tmp_path):
    out = site.build(tmp_path / "site", tmp_path / "missing", root=".")
    page = (out / "strategies" / "nothing-ever-happens.html").read_text()
    assert "No paper runs yet." in page
    assert "<svg" not in page


def test_single_run_still_draws(tmp_path):
    state = tmp_path / "s"
    state.mkdir()
    write_runs(state / "nothing-ever-happens.runs.jsonl", [run(1794268800, -31.15)])
    page = site.build(tmp_path / "o", state, root=".") / "strategies/nothing-ever-happens.html"
    text = page.read_text()
    assert text.count('class="dot') == 3  # two P&L series and capital at risk


def test_chart_data_cannot_close_its_script_tag():
    html = site.line_chart(
        "c", [0, 1], [{"label": "</script><b>x", "values": [1.0, 2.0], "slot": 1}]
    )
    assert "</script><b>" not in html.split('type="application/json"')[1].split("</script>")[0]


def test_nice_ticks_cover_the_range():
    ticks = site.nice_ticks(-34.0, 0.0)
    assert ticks[0] <= -34 and ticks[-1] >= 0 and 0 in ticks


def test_money_format():
    assert site.money(-29.9, signed=True) == "−$29.90"
    assert site.money(1234.5, signed=True) == "+$1,234.50"


def test_public_run_logs_serves_only_top_level_run_logs(tmp_path):
    write_runs(tmp_path / "a.runs.jsonl", [run(1, 0)])
    (tmp_path / "a.sqlite").write_text("journal")
    (tmp_path / "KILL").write_text("")
    (tmp_path / "collect").mkdir()
    (tmp_path / "collect" / "runs.jsonl").write_text("{}\n")
    assert list(site.public_run_logs(tmp_path)) == ["a.runs.jsonl"]


def test_safe_log_name():
    assert site.safe_log_name("nothing-ever-happens.kalshi-demo.runs.jsonl")
    for bad in ["../x.runs.jsonl", "/x.runs.jsonl", "d/x.runs.jsonl", "..\\x.runs.jsonl", "x.db"]:
        assert not site.safe_log_name(bad)


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self.body


def test_fetch_from_url_writes_logs_and_rejects_paths(tmp_path, monkeypatch):
    import urllib.request

    body = {"files": {"a.runs.jsonl": '{"ts": 1}\n'}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Response(body))
    assert site.fetch_from_url(tmp_path / "s", "https://example.test/logs") == ["a.runs.jsonl"]
    assert (tmp_path / "s" / "a.runs.jsonl").read_text() == '{"ts": 1}\n'

    body = {"files": {"../escape.runs.jsonl": "x"}}
    try:
        site.fetch_from_url(tmp_path / "t", "https://example.test/logs")
    except ValueError:
        pass
    else:
        raise AssertionError("path name accepted")
    assert not (tmp_path / "escape.runs.jsonl").exists()


def test_fetch_from_url_requires_https(tmp_path):
    try:
        site.fetch_from_url(tmp_path, "file:///etc/passwd")
    except ValueError:
        return
    raise AssertionError("non-https URL accepted")
