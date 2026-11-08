import base64
from decimal import Decimal as D

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from predmark.execution.engine import Engine
from predmark.execution.fees import kalshi_fee, polymarket_fee
from predmark.execution.kalshi import (
    KalshiVenue,
    LiveTradingNotArmed,
    fmt_exact,
    parse_book,
    sign_request,
)
from predmark.execution.models import (
    Book,
    OrderRequest,
    OrderStatus,
    Side,
    TimeInForce,
    VenueDisabled,
    VenueRejected,
    VenueUnknown,
)
from predmark.execution.paper import PaperVenue
from predmark.execution.polymarket import PolymarketBooks
from predmark.execution.risk import Ledger, MarketPosition, RiskLimits, worst_case_loss

T = "TEST-1"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Books:
    def __init__(self, clock, bid="0.40", ask="0.50", size="100"):
        self.clock = clock
        self.set(bid, ask, size)

    def set(self, bid, ask, size="100"):
        self.bid, self.ask, self.size = bid, ask, size

    def __call__(self, ticker):
        return Book(
            ticker,
            ((D(self.bid), D(self.size)),) if self.bid else (),
            ((D(self.ask), D(self.size)),) if self.ask else (),
            self.clock(),
        )


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    books = Books(clock)
    venue = PaperVenue(books, cash=D(1000), clock=clock)
    engine = Engine(venue, tmp_path / "j.sqlite", kill_file=tmp_path / "KILL", clock=clock)
    return engine, venue, books, clock, tmp_path


def buy(count="10", price="0.50", **kw):
    return OrderRequest(T, Side.BUY, D(count), D(price), **kw)


def sell(count="10", price="0.40", **kw):
    return OrderRequest(T, Side.SELL, D(count), D(price), **kw)


# fees --------------------------------------------------------------------------------------


def test_kalshi_taker_fee_matches_schedule():
    assert kalshi_fee(D(100), D("0.50")) == D("1.75")


def test_kalshi_fee_rounds_up_to_the_cent():
    assert kalshi_fee(D(1), D("0.50")) == D("0.02")


def test_kalshi_maker_is_free_unless_series_charges_it():
    assert kalshi_fee(D(100), D("0.50"), is_taker=False) == 0
    charged = kalshi_fee(D(100), D("0.50"), False, "quadratic_with_maker_fees")
    assert charged == D("0.44")


def test_polymarket_fee_by_category():
    assert polymarket_fee(D(100), D("0.50"), "crypto") == D("1.75")
    assert polymarket_fee(D(100), D("0.50"), "geopolitics") == 0
    assert polymarket_fee(D(100), D("0.50"), "crypto", is_taker=False) == 0


# ledger and risk ---------------------------------------------------------------------------


def test_worst_case_loss_long_short_and_flat():
    assert worst_case_loss(MarketPosition(D(10), D(5))) == D(5)  # long 10 at .50: NO loses 5
    assert worst_case_loss(MarketPosition(D(-10), D(-4))) == D(6)  # short at .40: YES loses 6
    assert worst_case_loss(None) == 0


def test_worst_case_loss_counts_resting_orders():
    assert worst_case_loss(None, [(Side.BUY, D(10), D("0.30"))]) == D(3)
    assert worst_case_loss(None, [(Side.SELL, D(10), D("0.30"))]) == D(7)


def test_hedged_position_reduces_worst_case():
    # Long 10 at .50, then a resting sell at .60 cannot increase the loss.
    assert worst_case_loss(MarketPosition(D(10), D(5)), [(Side.SELL, D(10), D("0.60"))]) == D(5)


def test_nav_marks_longs_at_bid_and_shorts_at_ask():
    ledger = Ledger({T: MarketPosition(D(10), D(5))})
    book = Book(T, ((D("0.40"), D(1)),), ((D("0.50"), D(1)),), 0)
    assert ledger.nav({T: book}) == D(-1)
    ledger = Ledger({T: MarketPosition(D(-10), D(-4))})
    assert ledger.nav({T: book}) == D(-1)


# engine ------------------------------------------------------------------------------------


def test_taker_fill_updates_ledger(rig):
    engine, venue, *_ = rig
    assert engine.submit(buy()) is OrderStatus.FILLED
    assert engine.ledger.contracts(T) == D(10)
    assert engine.reconcile() == []


def test_sync_is_idempotent(rig):
    engine, *_ = rig
    engine.submit(buy())
    engine.sync_fills()
    engine.sync_fills()
    assert engine.ledger.contracts(T) == D(10)


def test_ledger_survives_restart(rig):
    engine, venue, books, clock, tmp = rig
    engine.submit(buy())
    again = Engine(venue, tmp / "j.sqlite", kill_file=tmp / "KILL", clock=clock)
    assert again.ledger.contracts(T) == D(10)
    assert again.reconcile() == []


def test_order_size_limit_rejects_without_reaching_venue(rig):
    engine, venue, *_ = rig
    assert engine.submit(buy(count="101")) is OrderStatus.REJECTED
    assert venue.positions() == {}


def test_sell_notional_uses_dollars_at_risk(rig):
    engine, *_ = rig
    # Selling 40 at .10 risks $36, above the $25 limit, though 40 * .10 is only $4.
    engine.limits = RiskLimits(price_collar=D(1))
    assert engine.submit(sell(count="40", price="0.10")) is OrderStatus.REJECTED


def test_collar_rejects_fat_finger(rig):
    engine, venue, *_ = rig
    assert engine.submit(buy(price="0.90")) is OrderStatus.REJECTED
    assert venue.positions() == {}


def test_stale_book_rejected(rig, tmp_path):
    engine, venue, books, clock, _ = rig
    venue.book_source = lambda t: Book(t, (), (), clock() - 60)
    assert engine.submit(buy()) is OrderStatus.REJECTED


def test_resting_orders_count_toward_limits(rig):
    engine, *_ = rig
    engine.limits = RiskLimits(max_market_loss=D(5))
    assert engine.submit(buy(count="10", price="0.30")) is OrderStatus.RESTING  # $3
    assert engine.submit(buy(count="10", price="0.30")) is OrderStatus.REJECTED  # would be $6


def test_kill_file_blocks_orders(rig):
    engine, venue, books, clock, tmp = rig
    (tmp / "KILL").touch()
    assert engine.submit(buy()) is OrderStatus.REJECTED
    assert venue.positions() == {}


def test_daily_loss_halts_and_cancels(rig):
    engine, venue, books, clock, _ = rig
    engine.limits = RiskLimits(max_daily_loss=D(3))
    engine.submit(buy())
    engine.check_daily_loss()
    assert engine.submit(buy(price="0.30")) is OrderStatus.RESTING
    books.set("0.05", "0.50")  # loss of $3.50 against a $3 limit
    engine.check_daily_loss()
    assert "daily loss" in engine.halt_reason
    assert venue.open_orders() == []
    assert engine.submit(buy()) is OrderStatus.REJECTED


class FlakyVenue(PaperVenue):
    """Applies the order at the venue, then loses the response."""

    def place(self, req):
        super().place(req)
        raise VenueUnknown("timeout after send")


def test_ambiguous_send_halts_and_reconcile_recovers_the_fill(tmp_path):
    clock = Clock()
    venue = FlakyVenue(Books(clock), clock=clock)
    engine = Engine(venue, tmp_path / "j.sqlite", kill_file=tmp_path / "K", clock=clock)
    req = buy()
    assert engine.submit(req) is OrderStatus.UNKNOWN
    assert engine.halt_reason
    assert engine.submit(buy()) is OrderStatus.REJECTED  # no blind resubmission
    assert len(venue.positions()) == 1 and venue.positions()[T] == D(10)
    assert engine.resume() is True
    assert engine.ledger.contracts(T) == D(10)
    row = engine.db.execute(
        "SELECT status FROM orders WHERE client_order_id = ?", (req.client_order_id,)
    ).fetchone()
    assert row["status"] == "filled"


class LostVenue(PaperVenue):
    def place(self, req):
        raise VenueUnknown("connection reset before send")


def test_unknown_order_stays_unresolved_until_grace_elapses(tmp_path):
    clock = Clock()
    venue = LostVenue(Books(clock), clock=clock)
    engine = Engine(venue, tmp_path / "j.sqlite", kill_file=tmp_path / "K", clock=clock)
    engine.submit(buy())
    assert engine.resume() is False
    clock.now += 31
    assert engine.resume() is True


def test_external_position_blocks_resume(rig):
    engine, venue, books, clock, _ = rig
    venue._pos["OTHER"] = D(5)
    assert any("OTHER" in p for p in engine.reconcile())
    assert engine.halt_reason
    assert engine.resume() is False


def test_settlement_realizes_pnl_and_reconciles(rig):
    engine, venue, *_ = rig
    engine.submit(buy())  # 10 @ .50 plus fee
    fee = engine.ledger.markets[T].fees
    engine.apply_settlement(T, D(1))
    assert engine.ledger.realized == D(5) - fee
    assert T not in engine.ledger.markets


# paper venue -------------------------------------------------------------------------------


def test_paper_resting_needs_strict_cross(rig):
    engine, venue, books, clock, _ = rig
    assert engine.submit(buy(price="0.45")) is OrderStatus.RESTING
    books.set("0.40", "0.45")
    venue.book(T)
    assert venue.positions() == {}  # touched, not crossed
    books.set("0.40", "0.44")
    venue.book(T)
    assert venue.positions()[T] == D(10)
    engine.sync_fills()
    assert engine.ledger.markets[T].fees == 0  # maker


def test_paper_post_only_cannot_cross(rig):
    engine, *_ = rig
    assert engine.submit(buy(post_only=True)) is OrderStatus.REJECTED


def test_paper_fok_needs_full_size(rig):
    engine, venue, books, *_ = rig
    books.set("0.40", "0.50", size="5")
    assert engine.submit(buy(tif=TimeInForce.FOK)) is OrderStatus.REJECTED
    assert venue.positions() == {}


def test_paper_ioc_partial(rig):
    engine, venue, books, *_ = rig
    books.set("0.40", "0.50", size="4")
    engine.submit(buy(tif=TimeInForce.IOC))
    assert venue.positions()[T] == D(4)
    assert venue.open_orders() == []


def test_paper_rejects_insufficient_balance(tmp_path):
    clock = Clock()
    venue = PaperVenue(Books(clock), cash=D(1), clock=clock)
    with pytest.raises(VenueRejected):
        venue.place(buy())


# kalshi adapter ----------------------------------------------------------------------------


def test_prod_requires_every_arming_step(monkeypatch):
    key = ed25519.Ed25519PrivateKey.generate()
    monkeypatch.delenv("PREDMARK_ARM_LIVE", raising=False)
    with pytest.raises(LiveTradingNotArmed):
        KalshiVenue("k", key, env="prod")
    with pytest.raises(LiveTradingNotArmed):
        KalshiVenue("k", key, env="prod", allow_live=True)
    monkeypatch.setenv("PREDMARK_ARM_LIVE", "yes")
    with pytest.raises(LiveTradingNotArmed):
        KalshiVenue("k", key, env="prod", allow_live=True)
    monkeypatch.setenv("PREDMARK_ARM_LIVE", "trade-real-money")
    assert KalshiVenue("k", key, env="prod", allow_live=True).env == "prod"


def test_default_env_is_demo():
    venue = KalshiVenue("k", ed25519.Ed25519PrivateKey.generate())
    assert "demo" in str(venue.http.base_url)


def test_ed25519_signature_strips_query():
    key = ed25519.Ed25519PrivateKey.generate()
    sig = sign_request(key, "1", "get", "/trade-api/v2/portfolio/orders?limit=5")
    key.public_key().verify(base64.b64decode(sig), b"1GET/trade-api/v2/portfolio/orders")


def test_rsa_signature_is_pss_sha256():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = sign_request(key, "1", "POST", "/trade-api/v2/x")
    key.public_key().verify(
        base64.b64decode(sig),
        b"1POST/trade-api/v2/x",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def test_book_converts_no_bids_to_yes_asks():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.07", "2000"], ["0.11", "100"]],
            "no_dollars": [["0.54", "2000"], ["0.64", "100"]],
        }
    }
    book = parse_book(T, payload, 0)
    assert book.best_bid == D("0.11")
    assert book.best_ask == D("0.36")
    assert book.asks[1][0] == D("0.46")


def test_fmt_exact_refuses_to_round():
    assert fmt_exact(D("0.5"), 4, "price") == "0.5000"
    with pytest.raises(VenueRejected):
        fmt_exact(D("0.12345"), 4, "price")


def _venue(handler):
    return KalshiVenue(
        "k", ed25519.Ed25519PrivateKey.generate(), transport=httpx.MockTransport(handler)
    )


def test_place_sends_v2_body_and_signed_full_path():
    seen = {}

    def handler(request):
        import json

        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(
            201,
            json={"order_id": "o1", "fill_count": "0.00", "remaining_count": "10.00", "ts_ms": 1},
        )

    result = _venue(handler).place(buy(price="0.5"))
    assert seen["path"] == "/trade-api/v2/portfolio/events/orders"
    assert seen["body"]["side"] == "bid"
    assert seen["body"]["count"] == "10.00"
    assert seen["body"]["price"] == "0.5000"
    assert seen["body"]["self_trade_prevention_type"] == "taker_at_cross"
    assert "KALSHI-ACCESS-SIGNATURE" in seen["headers"]
    assert result.status is OrderStatus.RESTING


@pytest.mark.parametrize(
    "response,exc",
    [
        (httpx.Response(400, json={"error": "bad"}), VenueRejected),
        (httpx.Response(429, json={"error": "too many requests"}), VenueRejected),
        (httpx.Response(500), VenueUnknown),
        (httpx.Response(503), VenueUnknown),
    ],
)
def test_place_error_classification(response, exc):
    with pytest.raises(exc):
        _venue(lambda r: response).place(buy())


def test_place_timeout_is_unknown_and_sent_once():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(VenueUnknown):
        _venue(handler).place(buy())
    assert len(calls) == 1


def test_polymarket_orders_are_disabled():
    with pytest.raises(VenueDisabled):
        PolymarketBooks().place(buy())
