"""The Kalshi demo lifecycle check, run offline against the paper venue."""

import time
from decimal import Decimal as D

from predmark.execution import demo_check
from predmark.execution.models import Book, OrderStatus
from predmark.execution.paper import PaperVenue

T = "DEMO-1"


def books(ticker):
    return Book(ticker, ((D("0.40"), D(100)),), ((D("0.45"), D(100)),), time.time())


def run(venue, tmp_path, **kw):
    lines = []
    steps = demo_check.run_lifecycle(
        venue, tmp_path / "j.sqlite", tmp_path / "KILL", T, log=lines.append, **kw
    )
    return steps, lines


def test_every_step_passes_on_a_well_behaved_venue(tmp_path):
    venue = PaperVenue(books)
    steps, _ = run(venue, tmp_path)
    assert [(s.name, s.ok) for s in steps] == [
        ("start", True),
        ("rest", True),
        ("cancel", True),
        ("fill", True),
        ("close", True),
        ("crash", True),
    ]
    assert venue.open_orders() == []
    assert venue.positions()[T] == 0


def test_a_second_run_reuses_the_journal(tmp_path):
    venue = PaperVenue(books)
    run(venue, tmp_path)
    steps, _ = run(venue, tmp_path)
    assert all(s.ok for s in steps) and len(steps) == 6


class LateFills(PaperVenue):
    """Reports each fill only on the second request after it happens, as an exchange may."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.asked = 0

    def fills(self, since_ts):
        self.asked += 1
        return super().fills(since_ts) if self.asked % 2 == 0 else []


def test_waits_for_late_fills(tmp_path):
    slept = []
    steps, _ = run(LateFills(books), tmp_path, sleep=slept.append)
    assert all(s.ok for s in steps), steps
    assert slept


class NeverFills(PaperVenue):
    def fills(self, since_ts):
        return []


def test_failure_stops_and_cancels_open_orders(tmp_path):
    venue = NeverFills(books)
    steps, lines = run(venue, tmp_path, wait_seconds=0)
    assert [s.name for s in steps if s.ok] == ["start", "rest", "cancel"]
    assert steps[-1].name == "fill" and not steps[-1].ok
    assert "journal position 0, expected 1" in steps[-1].detail
    assert any(line.startswith("FAIL fill") for line in lines)
    assert venue.open_orders() == []


def test_external_position_fails_at_start(tmp_path):
    venue = PaperVenue(books)
    venue._pos["OTHER-1"] = D(3)
    steps, _ = run(venue, tmp_path)
    assert [(s.name, s.ok) for s in steps] == [("start", False)]
    assert "position mismatch on OTHER-1" in steps[0].detail


def test_lost_response_still_reaches_the_venue():
    venue = PaperVenue(books)
    lossy = demo_check.LostResponse(venue)
    from predmark.execution.models import OrderRequest, Side, VenueUnknown

    req = OrderRequest(T, Side.BUY, D(1), D("0.01"), post_only=True)
    try:
        lossy.place(req)
    except VenueUnknown:
        pass
    else:
        raise AssertionError("expected VenueUnknown")
    assert venue.find_order(req.client_order_id, 0).status is OrderStatus.RESTING


def test_pick_market_takes_the_tightest_two_sided_quote():
    def m(t, bid, ask, size="10", shard=0):
        return {
            "ticker": t,
            "exchange_index": shard,
            "yes_bid_dollars": bid,
            "yes_ask_dollars": ask,
            "yes_bid_size_fp": size,
            "yes_ask_size_fp": size,
        }

    markets = [
        m("WIDE", "0.30", "0.60"),
        m("TIGHT", "0.50", "0.52"),
        m("EDGE", "0.01", "0.02"),
        m("THIN", "0.50", "0.51", size="0.5"),
        m("ONESIDED", None, "0.50"),
        m("OTHERSHARD", "0.50", "0.505", shard=3),
    ]
    assert demo_check.pick_market(markets) == "TIGHT"
    assert demo_check.pick_market([m("EDGE", "0.01", "0.02")]) is None


def test_transfer_body_is_in_centicents_and_checked():
    import pytest

    from predmark.execution.demo_accounts import transfer_body

    body = transfer_body(0, 0, 2, 2, D("10.5"))
    assert body["amount"] == 105_000
    assert (body["source"], body["destination"]) == ("event_contract", "event_contract")
    assert (body["source_subaccount"], body["destination_subaccount"]) == (0, 2)
    assert (body["source_exchange_shard"], body["destination_exchange_shard"]) == (0, 2)
    assert "client_transfer_id" not in body  # Kalshi refuses it when a subaccount is involved
    primary = transfer_body(0, 0, 0, 2, D(1))["client_transfer_id"]
    assert primary != transfer_body(0, 0, 0, 2, D(1))["client_transfer_id"]
    for bad in [
        (0, 0, 2, 2, D("0.00001")),
        (0, 0, 2, 2, D(0)),
        (0, 0, 64, 0, D(1)),
        (1, 0, 1, 0, D(1)),
    ]:
        with pytest.raises(ValueError):
            transfer_body(*bad)
