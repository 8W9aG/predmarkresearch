"""Kalshi adapter on the V2 order API (POST/DELETE /portfolio/events/orders).

Defaults to the demo exchange. Production requires all of: env="prod", allow_live=True in code,
and PREDMARK_ARM_LIVE=trade-real-money in the environment.
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .models import (
    Book,
    Fill,
    OrderRequest,
    OrderStatus,
    PlaceResult,
    Side,
    VenueOrder,
    VenueRejected,
    VenueUnknown,
)

D = Decimal
URLS = {
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
    "prod": "https://external-api.kalshi.com/trade-api/v2",
}
ARM_VALUE = "trade-real-money"


class LiveTradingNotArmed(RuntimeError):
    pass


def sign_request(private_key: Any, timestamp_ms: str, method: str, path: str) -> str:
    """Sign timestamp + METHOD + full path, with the query string removed."""
    message = (timestamp_ms + method.upper() + path.split("?")[0]).encode()
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return base64.b64encode(private_key.sign(message)).decode()
    if isinstance(private_key, rsa.RSAPrivateKey):
        signature = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()
    raise TypeError(f"unsupported key type {type(private_key).__name__}")


def fmt_exact(value: Decimal, places: int, what: str) -> str:
    """Format without rounding. A size or price that does not fit is refused, not altered."""
    try:
        quantized = value.quantize(D(1).scaleb(-places))
    except InvalidOperation as exc:
        raise VenueRejected(f"{what} {value} not representable") from exc
    if quantized != value:
        raise VenueRejected(f"{what} {value} has more than {places} decimal places")
    return format(quantized, "f")


def parse_book(ticker: str, payload: dict[str, Any], ts: float) -> Book:
    """Kalshi quotes YES bids and NO bids; a NO bid at p is a YES ask at 1 - p."""
    fp = payload.get("orderbook_fp") or {}
    bids = sorted(((D(p), D(q)) for p, q in fp.get("yes_dollars") or []), reverse=True)
    asks = sorted((1 - D(p), D(q)) for p, q in fp.get("no_dollars") or [])
    return Book(ticker, tuple(bids), tuple(asks), ts)


def parse_ts(value: str | None) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _venue_order(o: dict[str, Any]) -> VenueOrder:
    status = {
        "resting": OrderStatus.RESTING,
        "canceled": OrderStatus.CANCELED,
        "executed": OrderStatus.FILLED,
    }[o["status"]]
    return VenueOrder(
        order_id=o["order_id"],
        client_order_id=o.get("client_order_id") or None,
        ticker=o["ticker"],
        side=Side(o["book_side"]),
        price=D(o["yes_price_dollars"]),
        remaining=D(o.get("remaining_count_fp") or "0"),
        status=status,
    )


class KalshiVenue:
    name = "kalshi"

    def __init__(
        self,
        key_id: str,
        private_key: Any,
        env: str = "demo",
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        if env not in URLS:
            raise ValueError(f"unknown env {env}")
        if env == "prod" and not (allow_live and os.environ.get("PREDMARK_ARM_LIVE") == ARM_VALUE):
            raise LiveTradingNotArmed(
                "Production needs allow_live=True and "
                f"PREDMARK_ARM_LIVE={ARM_VALUE}. Only the account owner should set these."
            )
        self.env = env
        self.key_id = key_id
        self.private_key = private_key
        self.base_path = urlsplit(URLS[env]).path
        self.http = httpx.Client(base_url=URLS[env], timeout=timeout, transport=transport)

    @classmethod
    def from_env(cls, env: str = "demo", allow_live: bool = False) -> KalshiVenue:
        key_id = os.environ["KALSHI_KEY_ID"]
        pem = Path(os.environ["KALSHI_PRIVATE_KEY_PATH"]).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        return cls(key_id, key, env=env, allow_live=allow_live)

    def _headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign_request(
                self.private_key, ts, method, self.base_path + path
            ),
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.http.get(path, params=params, headers=self._headers("GET", path))
        resp.raise_for_status()
        return resp.json()

    def _pages(self, path: str, key: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        cursor = ""
        while True:
            data = self._get(
                path, {**params, "limit": 1000, **({"cursor": cursor} if cursor else {})}
            )
            yield from data.get(key) or []
            cursor = data.get("cursor") or ""
            if not cursor:
                return

    def book(self, ticker: str) -> Book:
        ts = time.time()
        return parse_book(ticker, self._get(f"/markets/{ticker}/orderbook", {"depth": 25}), ts)

    def place(self, req: OrderRequest) -> PlaceResult:
        body = {
            "ticker": req.ticker,
            "side": req.side.value,
            "count": fmt_exact(req.count, 2, "count"),
            "price": fmt_exact(req.price, 4, "price"),
            "time_in_force": req.tif.value,
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": req.client_order_id,
            "post_only": req.post_only,
            "cancel_order_on_pause": True,
        }
        path = "/portfolio/events/orders"
        try:
            resp = self.http.post(path, json=body, headers=self._headers("POST", path))
        except httpx.TransportError as exc:
            raise VenueUnknown(f"transport error: {exc}") from exc
        if resp.status_code >= 500:
            raise VenueUnknown(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise VenueRejected(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        filled, remaining = D(data["fill_count"]), D(data["remaining_count"])
        if remaining == 0:
            status = OrderStatus.FILLED
        elif req.tif.value == "good_till_canceled":
            status = OrderStatus.RESTING
        else:
            status = OrderStatus.CANCELED
        return PlaceResult(data["order_id"], status, filled, remaining)

    def cancel(self, order_id: str, ticker: str) -> None:
        path = f"/portfolio/events/orders/{order_id}"
        try:
            resp = self.http.delete(
                path, params={"market_ticker": ticker}, headers=self._headers("DELETE", path)
            )
        except httpx.TransportError as exc:
            raise VenueUnknown(f"transport error: {exc}") from exc
        if resp.status_code >= 500:
            raise VenueUnknown(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise VenueRejected(f"HTTP {resp.status_code}: {resp.text[:300]}")

    def open_orders(self) -> list[VenueOrder]:
        rows = self._pages("/portfolio/orders", "orders", {"status": "resting"})
        return [_venue_order(o) for o in rows]

    def find_order(self, client_order_id: str, since_ts: float) -> VenueOrder | None:
        for o in self._pages("/portfolio/orders", "orders", {"min_ts": int(since_ts)}):
            if o.get("client_order_id") == client_order_id:
                return _venue_order(o)
        return None

    def positions(self) -> dict[str, Decimal]:
        rows = self._pages("/portfolio/positions", "market_positions", {})
        return {p["ticker"]: D(p["position_fp"]) for p in rows}

    def fills(self, since_ts: float) -> list[Fill]:
        out = []
        for f in self._pages("/portfolio/fills", "fills", {"min_ts": int(since_ts)}):
            out.append(
                Fill(
                    fill_id=f["fill_id"],
                    order_id=f["order_id"],
                    ticker=f["ticker"],
                    side=Side(f["book_side"]),
                    count=D(f["count_fp"]),
                    price=D(f["yes_price_dollars"]),
                    fee=D(f["fee_cost"]),
                    is_taker=bool(f["is_taker"]),
                    ts=parse_ts(f.get("created_time")) or float(f.get("ts") or 0),
                )
            )
        return out

    def balance(self) -> Decimal:
        return D(self._get("/portfolio/balance")["balance_dollars"])
