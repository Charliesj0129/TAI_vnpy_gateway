"""
Helpers that adapt Fubon websocket JSON payloads into vn.py friendly
TickData and TradeData objects, and prepare structured rows for persistence.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

try:  # pragma: no cover - Python 3.9+ ?? zoneinfo
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment, misc]

from vnpy_fubon.normalization import normalize_exchange, normalize_symbol
from vnpy_fubon.vnpy_compat import Direction, Exchange, Offset, TickData, TradeData

LOGGER = logging.getLogger("vnpy_fubon.adapters")

TAIWAN_TZ = ZoneInfo("Asia/Taipei") if ZoneInfo else timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- #
# ??


@dataclass
class RawEnvelope:
    """Wrapper for a raw websocket message with normalised metadata."""

    symbol: str
    channel: str
    payload: Mapping[str, Any]
    event_ts_utc: datetime
    event_ts_local: datetime
    seq: Optional[int] = None
    checksum: Optional[str] = None
    latency_ms: Optional[int] = None

    def dedup_token(self) -> str:
        base = f"{self.symbol}|{self.channel}|{self.seq or ''}|{self.checksum or ''}|{int(self.event_ts_utc.timestamp() * 1000)}"
        if self.seq is None and self.checksum is None:
            raw_json = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
            digest = hashlib.sha1(raw_json.encode("utf-8")).hexdigest()
            base = f"{base}|{digest}"
        return base


@dataclass
class BookRow:
    """Database row representation for depth-of-book snapshots."""

    symbol: str
    event_ts_utc: datetime
    event_ts_local: datetime
    level: int
    bid_px: Optional[Decimal]
    bid_sz: Optional[Decimal]
    ask_px: Optional[Decimal]
    ask_sz: Optional[Decimal]
    mid_px: Optional[Decimal]
    book_seq: Optional[int]
    is_snapshot: bool
    channel: str
    checksum: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "event_ts_utc": self.event_ts_utc,
            "event_ts_local": self.event_ts_local,
            "level": self.level,
            "bid_px": self.bid_px,
            "bid_sz": self.bid_sz,
            "ask_px": self.ask_px,
            "ask_sz": self.ask_sz,
            "mid_px": self.mid_px,
            "book_seq": self.book_seq,
            "is_snapshot": self.is_snapshot,
            "channel": self.channel,
            "checksum": self.checksum,
        }


@dataclass
class NormalizedTrade:
    trade: TradeData
    row: Dict[str, Any]
    raw: RawEnvelope


@dataclass
class NormalizedOrderBook:
    tick: TickData
    rows: List[BookRow]
    raw: RawEnvelope


@dataclass
class NormalizedQuote:
    tick: TickData
    row: Dict[str, Any]
    raw: RawEnvelope


# --------------------------------------------------------------------------- #
# ??????


def _first(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return default


def _to_decimal(value: Any, fallback: str = "0") -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, "", "null"):
        return Decimal(fallback)
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    if not text:
        return Decimal(fallback)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal(fallback)


def _ensure_datetime(value: Any) -> datetime:
    """Convert various timestamp formats to a timezone-aware UTC datetime."""

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000_000_000:  # nanoseconds
            timestamp /= 1_000_000_000.0
        elif timestamp > 1_000_000_000_000_000:  # microseconds
            timestamp /= 1_000_000.0
        elif timestamp > 1_000_000_000_000:  # milliseconds
            timestamp /= 1_000.0
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        for parser in (
            datetime.fromisoformat,
            lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
            lambda s: datetime.strptime(s, "%Y/%m/%d %H:%M:%S"),
            lambda s: datetime.strptime(s, "%Y%m%d%H%M%S"),
        ):
            try:
                dt = parser(text)
                break
            except Exception:
                continue
        else:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_and_local(event_dt: datetime) -> Tuple[datetime, datetime]:
    utc_dt = event_dt.astimezone(timezone.utc)
    local_dt = utc_dt.astimezone(TAIWAN_TZ)
    return utc_dt, local_dt


def _resolve_direction(raw: Any) -> Direction:
    text = str(raw or "").strip().lower()
    if text in {"b", "buy", "long"}:
        return Direction.LONG
    if text in {"s", "sell", "short"}:
        return Direction.SHORT
    return Direction.NET


def _seq_from_payload(payload: Mapping[str, Any]) -> Optional[int]:
    seq = _first(payload, "seq", "bookSeq", "orderSeq", "quoteSeq", "matchSeq", "matchNo", "serial")
    if seq is None:
        return None
    try:
        return int(seq)
    except (TypeError, ValueError):
        return None


def _checksum_from_payload(payload: Mapping[str, Any]) -> Optional[str]:
    checksum = _first(payload, "checksum", "crc", "md5")
    if checksum:
        return str(checksum)
    return None


def _flatten_market_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Fubon websocket payloads wrap the useful fields under a ``data`` key.
    Flatten those nested mappings (while keeping the original under ``data``)
    so downstream normalisation code can rely on a single-level lookup.
    """

    if not isinstance(payload, Mapping):
        return payload

    merged: Dict[str, Any] = dict(payload)
    stack: List[Mapping[str, Any]] = []

    for key in ("data", "payload"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            stack.append(nested)

    seen: set[int] = set()
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)

        for key, value in current.items():
            existing = merged.get(key)
            if key not in merged or existing in (None, "", (), [], {}):
                merged[key] = value
            if isinstance(value, Mapping):
                stack.append(value)

    return merged


# --------------------------------------------------------------------------- #
# ?


class MarketEnvelopeNormalizer:
    """Convert Fubon websocket payloads into vn.py compatible data structures."""

    def __init__(self, *, gateway_name: str = "FubonIngest") -> None:
        self.gateway_name = gateway_name

    # ------------------------ raw envelope helpers ------------------------ #

    def build_raw_envelope(
        self,
        payload: Mapping[str, Any],
        *,
        default_channel: str,
        latency_ms: Optional[int] = None,
    ) -> RawEnvelope:
        payload = _flatten_market_payload(payload)
        channel = str(_first(payload, "channel", "topic", "type", default=default_channel)).lower()
        symbol = normalize_symbol(_first(payload, "symbol", "contractId", "code", "contractID"))
        if not symbol:
            LOGGER.debug("payload ? symbol?s", payload)
        event_dt = _ensure_datetime(
            _first(
                payload,
                "exchangeTime",
                "matchTime",
                "transactionTime",
                "updateTime",
                "timestamp",
                "time",
            )
        )
        event_ts_utc, event_ts_local = _utc_and_local(event_dt)
        seq = _seq_from_payload(payload)
        checksum = _checksum_from_payload(payload)
        return RawEnvelope(
            symbol=symbol,
            channel=channel or default_channel,
            payload=payload,
            event_ts_utc=event_ts_utc,
            event_ts_local=event_ts_local,
            seq=seq,
            checksum=checksum,
            latency_ms=latency_ms,
        )

    # ----------------------------- trades -------------------------------- #

    def normalize_trade(
        self,
        payload: Mapping[str, Any],
        *,
        latency_ms: Optional[int] = None,
    ) -> NormalizedTrade:
        raw_env = self.build_raw_envelope(payload, default_channel="trades", latency_ms=latency_ms)
        payload = raw_env.payload
        exchange = normalize_exchange(_first(payload, "exchange", "market", default="TAIFEX"))

        trade_entry: Mapping[str, Any] | None = None
        trades = payload.get("trades")
        if isinstance(trades, Sequence) and trades:
            first_trade = trades[0]
            if isinstance(first_trade, Mapping):
                trade_entry = first_trade
        if trade_entry is None:
            trade_entry = payload

        price = _to_decimal(
            _first(
                trade_entry,
                "price",
                "matchPrice",
                "dealPrice",
                default=_first(payload, "price", "matchPrice", "dealPrice", default="0"),
            )
        )
        qty = _to_decimal(
            _first(
                trade_entry,
                "size",
                "qty",
                "volume",
                "matchQty",
                "dealVolume",
                default=_first(payload, "volume", "matchQty", "dealVolume", "qty", default="0"),
            )
        )
        turnover = price * qty

        trade_id = str(
            _first(
                payload,
                "matchNo",
                "tradeId",
                "serial",
                "seq",
                "id",
                default=f"{raw_env.symbol}-{int(raw_env.event_ts_utc.timestamp() * 1000)}",
            )
        )
        order_id = str(_first(payload, "orderId", "orderNo", default=""))
        direction = _resolve_direction(_first(trade_entry, "side", "bsFlag", "buySell", default=_first(payload, "side", "bsFlag", "buySell")))
        if direction is Direction.NET:
            bid_hint = trade_entry.get("bid")
            ask_hint = trade_entry.get("ask")
            try:
                bid_px = _to_decimal(bid_hint) if bid_hint is not None else None
                ask_px = _to_decimal(ask_hint) if ask_hint is not None else None
            except Exception:
                bid_px = ask_px = None
            if bid_px is not None and price <= bid_px:
                direction = Direction.SHORT
            elif ask_px is not None and price >= ask_px:
                direction = Direction.LONG

        trade = TradeData(
            symbol=raw_env.symbol,
            exchange=exchange,
            tradeid=trade_id,
            orderid=order_id,
            direction=direction,
            offset=Offset.NONE,
            price=price,
            volume=qty,
            datetime=raw_env.event_ts_local,
            gateway_name=self.gateway_name,
        )
        trade.extra = {
            "channel": raw_env.channel,
            "seq": raw_env.seq,
            "checksum": raw_env.checksum,
            "latency_ms": raw_env.latency_ms,
        }

        row = {
            "symbol": raw_env.symbol,
            "trade_id": trade_id,
            "event_seq": raw_env.seq,
            "side": direction.value,
            "price": price,
            "quantity": qty,
            "turnover": turnover,
            "event_ts_utc": raw_env.event_ts_utc,
            "event_ts_local": raw_env.event_ts_local,
            "channel": raw_env.channel,
            "checksum": raw_env.checksum,
        }

        return NormalizedTrade(trade=trade, row=row, raw=raw_env)

    # ---------------------------- order book ------------------------------ #

    def normalize_orderbook(
        self,
        payload: Mapping[str, Any],
        *,
        depth: int = 5,
        latency_ms: Optional[int] = None,
    ) -> NormalizedOrderBook:
        raw_env = self.build_raw_envelope(payload, default_channel="orderbook", latency_ms=latency_ms)
        payload = raw_env.payload
        exchange = normalize_exchange(_first(payload, "exchange", "market", default="TAIFEX"))

        last_price = _to_decimal(_first(payload, "lastPrice", "close", "referencePrice", default="0"))
        volume = _to_decimal(_first(payload, "totalVolume", "volume", default="0"))
        name = str(_first(payload, "name", "symbolName", "symbol", default=raw_env.symbol))

        tick = TickData(
            symbol=raw_env.symbol,
            exchange=exchange,
            datetime=raw_env.event_ts_local,
            name=name,
            last_price=last_price,
            volume=volume,
            gateway_name=self.gateway_name,
        )

        rows: List[BookRow] = []
        bids: Sequence[Any] | None = payload.get("bids") or payload.get("bidQuotes")
        asks: Sequence[Any] | None = payload.get("asks") or payload.get("askQuotes")

        if isinstance(bids, Sequence) and isinstance(asks, Sequence) and bids and asks:
            for level in range(1, depth + 1):
                bid_px, bid_sz = _extract_array_level(bids, level)
                ask_px, ask_sz = _extract_array_level(asks, level)
                rows.append(
                    BookRow(
                        symbol=raw_env.symbol,
                        event_ts_utc=raw_env.event_ts_utc,
                        event_ts_local=raw_env.event_ts_local,
                        level=level,
                        bid_px=bid_px,
                        bid_sz=bid_sz,
                        ask_px=ask_px,
                        ask_sz=ask_sz,
                        mid_px=_mid_price(bid_px, ask_px),
                        book_seq=raw_env.seq,
                        is_snapshot=_is_snapshot(payload),
                        channel=raw_env.channel,
                        checksum=raw_env.checksum,
                    )
                )
        else:
            for level in range(1, depth + 1):
                bid_px = _to_decimal(_first(payload, f"bidPx{level}", f"bidPrice{level}"), fallback="0")
                bid_sz = _to_decimal(_first(payload, f"bidSz{level}", f"bidVolume{level}", f"bidQty{level}"), fallback="0")
                ask_px = _to_decimal(_first(payload, f"askPx{level}", f"askPrice{level}"), fallback="0")
                ask_sz = _to_decimal(_first(payload, f"askSz{level}", f"askVolume{level}", f"askQty{level}"), fallback="0")
                rows.append(
                    BookRow(
                        symbol=raw_env.symbol,
                        event_ts_utc=raw_env.event_ts_utc,
                        event_ts_local=raw_env.event_ts_local,
                        level=level,
                        bid_px=bid_px,
                        bid_sz=bid_sz,
                        ask_px=ask_px,
                        ask_sz=ask_sz,
                        mid_px=_mid_price(bid_px, ask_px),
                        book_seq=raw_env.seq,
                        is_snapshot=_is_snapshot(payload),
                        channel=raw_env.channel,
                        checksum=raw_env.checksum,
                    )
                )

        if rows:
            first = rows[0]
            tick.bid_price_1 = first.bid_px
            tick.bid_volume_1 = first.bid_sz
            tick.ask_price_1 = first.ask_px
            tick.ask_volume_1 = first.ask_sz

        tick.extra = {
            "book_seq": raw_env.seq,
            "is_snapshot": _is_snapshot(payload),
            "channel": raw_env.channel,
            "checksum": raw_env.checksum,
            "latency_ms": raw_env.latency_ms,
            "levels": [row.as_dict() for row in rows],
        }

        return NormalizedOrderBook(tick=tick, rows=rows, raw=raw_env)

    # ---------------------------- quotes --------------------------------- #

    def normalize_quote(
        self,
        payload: Mapping[str, Any],
        *,
        latency_ms: Optional[int] = None,
    ) -> NormalizedQuote:
        raw_env = self.build_raw_envelope(payload, default_channel="quotes", latency_ms=latency_ms)
        payload = raw_env.payload
        exchange = normalize_exchange(_first(payload, "exchange", "market", default="TAIFEX"))

        last_price = _to_decimal(_first(payload, "lastPrice", "close", default="0"))
        open_price = _to_decimal(_first(payload, "openPrice", "open", default="0"))
        high_price = _to_decimal(_first(payload, "highPrice", "high", default="0"))
        low_price = _to_decimal(_first(payload, "lowPrice", "low", default="0"))
        volume = _to_decimal(_first(payload, "volume", "totalVolume", "accVolume", default="0"))
        turnover = _to_decimal(_first(payload, "turnover", "totalTurnover", default="0"))
        open_interest = _to_decimal(_first(payload, "openInterest", "oi"), fallback="0")
        est_settlement = _to_decimal(_first(payload, "settlementPrice", "theoreticalPrice"), fallback="0")
        implied_vol = _to_decimal(_first(payload, "impliedVol", "impliedVolatility"), fallback="0")

        tick = TickData(
            symbol=raw_env.symbol,
            exchange=exchange,
            datetime=raw_env.event_ts_local,
            name=str(_first(payload, "name", "symbolName", default=raw_env.symbol)),
            last_price=last_price,
            volume=volume,
            gateway_name=self.gateway_name,
        )

        tick.bid_price_1 = _to_decimal(_first(payload, "bidPx1", "bidPrice1"), fallback="0")
        tick.bid_volume_1 = _to_decimal(_first(payload, "bidSz1", "bidVolume1"), fallback="0")
        tick.ask_price_1 = _to_decimal(_first(payload, "askPx1", "askPrice1"), fallback="0")
        tick.ask_volume_1 = _to_decimal(_first(payload, "askSz1", "askVolume1"), fallback="0")

        tick.extra = {
            "channel": raw_env.channel,
            "book_seq": raw_env.seq,
            "checksum": raw_env.checksum,
            "latency_ms": raw_env.latency_ms,
            "quote": True,
        }

        row = {
            "symbol": raw_env.symbol,
            "event_ts_utc": raw_env.event_ts_utc,
            "event_ts_local": raw_env.event_ts_local,
            "last_px": last_price,
            "prev_close_px": _to_decimal(_first(payload, "prevClose", "previousClose"), fallback="0"),
            "open_px": open_price,
            "high_px": high_price,
            "low_px": low_price,
            "bid_px_1": tick.bid_price_1,
            "bid_sz_1": tick.bid_volume_1,
            "ask_px_1": tick.ask_price_1,
            "ask_sz_1": tick.ask_volume_1,
            "volume": volume,
            "turnover": turnover,
            "open_interest": open_interest,
            "implied_vol": implied_vol,
            "est_settlement": est_settlement,
            "book_seq": raw_env.seq,
            "checksum": raw_env.checksum,
            "channel": raw_env.channel,
        }

        return NormalizedQuote(tick=tick, row=row, raw=raw_env)


# --------------------------------------------------------------------------- #
# ?????????

def _extract_array_level(entries: Sequence[Any], level: int) -> Tuple[Decimal, Decimal]:
    index = level - 1
    if index >= len(entries):
        return Decimal("0"), Decimal("0")

    entry = entries[index]
    if isinstance(entry, Mapping):
        price = _to_decimal(_first(entry, "price", "px"))
        size = _to_decimal(_first(entry, "qty", "size", "volume"))
        return price, size
    if isinstance(entry, Sequence) and len(entry) >= 2:
        price = _to_decimal(entry[0])
        size = _to_decimal(entry[1])
        return price, size
    return Decimal("0"), Decimal("0")


def _is_snapshot(payload: Mapping[str, Any]) -> bool:
    flag = _first(payload, "isSnapshot", "snapshot")
    if isinstance(flag, bool):
        return flag
    text = str(flag or "").strip().lower()
    if text in {"y", "yes", "true", "snapshot"}:
        return True
    if text in {"n", "no", "false", "delta"}:
        return False
    return _first(payload, "seq", "bookSeq") is None  # ????????

def _mid_price(bid: Decimal, ask: Decimal) -> Optional[Decimal]:
    if bid is None or ask is None:
        return None
    if bid == Decimal("0") and ask == Decimal("0"):
        return None
    return (bid + ask) / Decimal("2")


__all__ = [
    "BookRow",
    "MarketEnvelopeNormalizer",
    "NormalizedOrderBook",
    "NormalizedQuote",
    "NormalizedTrade",
    "RawEnvelope",
]

# Legacy alias for external imports
FubonToVnpyAdapter = MarketEnvelopeNormalizer

