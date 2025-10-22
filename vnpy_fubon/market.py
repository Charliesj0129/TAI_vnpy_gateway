"""
Market data helpers for subscription and quote normalisation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from .exceptions import FubonSDKMethodNotFoundError
from .normalization import normalize_exchange
from .vnpy_compat import Exchange, TickData

SUBSCRIBE_METHODS = ("subscribe", "subscribe_market_data", "AddQuote")
UNSUBSCRIBE_METHODS = ("unsubscribe", "unsubscribe_market_data", "RemoveQuote")
FETCH_QUOTE_METHODS = ("fetch_quote", "get_quote", "QueryQuote")

LOGGER = logging.getLogger("vnpy_fubon.market")


@dataclass(frozen=True)
class MarketEvent:
    """
    Normalised result for websocket payload inspection.

    Attributes
    ----------
    channel:
        Lower-cased channel name if available (e.g. ``books`` or ``trades``).
    payload:
        Raw payload mapping supplied by the SDK for the event.
    tick:
        TickData representation when the payload matches order book semantics.
    event_type:
        Semantic classification of the payload; one of ``orderbook``, ``trade`` or ``other``.
    """

    channel: Optional[str]
    payload: Mapping[str, Any]
    tick: Optional[TickData]
    event_type: str


def _ensure_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except InvalidOperation:
            return Decimal("0")
    return Decimal("0")


def _normalize_exchange(raw: Any) -> Exchange:
    return normalize_exchange(raw)


class MarketAPI:
    """
    Handles market data subscriptions and converts raw quotes to TickData.
    """

    def __init__(
        self,
        client: Any,
        *,
        gateway_name: str = "Fubon",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.client = client
        self.gateway_name = gateway_name
        self.logger = logger or LOGGER

    def subscribe_quotes(
        self,
        symbols: Sequence[str],
        *,
        depth: Optional[int] = None,
        **kwargs: Any,
    ) -> Any:
        method = self._resolve_method(SUBSCRIBE_METHODS)
        payload = {"symbols": list(symbols)}
        if depth is not None:
            payload["depth"] = depth
        payload.update(kwargs)
        self.logger.debug("Subscribing quotes with payload %s", payload)
        return method(**payload)

    def unsubscribe_quotes(self, symbols: Sequence[str], **kwargs: Any) -> Any:
        method = self._resolve_method(UNSUBSCRIBE_METHODS)
        payload = {"symbols": list(symbols)}
        payload.update(kwargs)
        self.logger.debug("Unsubscribing quotes with payload %s", payload)
        return method(**payload)

    def fetch_quote(self, symbol: str, **kwargs: Any) -> TickData:
        method = self._resolve_method(FETCH_QUOTE_METHODS)
        payload = {"symbol": symbol}
        payload.update(kwargs)
        response = method(**payload)
        if not isinstance(response, Mapping):
            raise RuntimeError(f"Unexpected quote payload type: {type(response)}")
        return self._to_tick_data(response)

    def _resolve_method(self, candidates: Iterable[str]) -> Any:
        for name in candidates:
            method = getattr(self.client, name, None)
            if callable(method):
                return method
        raise FubonSDKMethodNotFoundError(
            f"MarketAPI cannot find any of {candidates} on client {type(self.client).__name__}"
        )

    def _to_tick_data(self, payload: Mapping[str, Any]) -> TickData:
        symbol = str(payload.get("symbol") or payload.get("code") or "")
        exchange = _normalize_exchange(payload.get("exchange"))
        timestamp = payload.get("timestamp") or payload.get("update_time") or payload.get("time")
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
                try:
                    dt = datetime.strptime(timestamp, fmt)
                    break
                except ValueError:
                    continue
            else:
                dt = datetime.now(timezone.utc)
        elif isinstance(timestamp, datetime):
            dt = timestamp
        else:
            dt = datetime.now(timezone.utc)

        tick = TickData(
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            name=str(payload.get("name") or payload.get("symbolName") or symbol),
            last_price=_ensure_decimal(payload.get("last_price") or payload.get("close") or payload.get("price")),
            volume=_ensure_decimal(payload.get("volume") or payload.get("totalVolume") or 0),
            bid_price_1=_ensure_decimal(payload.get("bid_price") or payload.get("bestBidPrice")),
            bid_volume_1=_ensure_decimal(payload.get("bid_volume") or payload.get("bestBidVolume")),
            ask_price_1=_ensure_decimal(payload.get("ask_price") or payload.get("bestAskPrice")),
            ask_volume_1=_ensure_decimal(payload.get("ask_volume") or payload.get("bestAskVolume")),
            gateway_name=self.gateway_name,
        )
        self.logger.debug("Mapped quote payload %s to %s", payload, tick)
        return tick

    # ------------------------------------------------------------------
    # Conversion helpers exposed for gateway usage

    def to_tick(self, payload: Mapping[str, Any]) -> TickData:
        """
        Public helper for converting a mapping to TickData.
        """

        return self._to_tick_data(payload)

    def parse_websocket_message(self, message: str) -> List[Tuple[TickData, Mapping[str, Any]]]:
        """
        Convert a websocket message into tick objects. Returns a list of (tick, raw_mapping) pairs.
        """

        items: List[Tuple[TickData, Mapping[str, Any]]] = []
        for event in self.parse_market_events(message):
            if event.tick is not None:
                items.append((event.tick, event.payload))
        return items

    def parse_market_events(self, message: str) -> List[MarketEvent]:
        """
        Decode a websocket message into structured market events.
        """

        try:
            decoded = json.loads(message)
        except Exception as exc:
            self.logger.debug("Unable to decode websocket message %s: %s", message, exc)
            return []

        events: List[MarketEvent] = []
        for channel, payload in self._expand_message(decoded):
            event_type = self._infer_event_type(channel, payload)
            tick: Optional[TickData] = None
            if event_type == "orderbook":
                try:
                    tick = self._to_tick_data(payload)
                except Exception as exc:  # pragma: no cover - vendor payload
                    self.logger.debug("Failed to map websocket payload %s: %s", payload, exc)
                    event_type = "other"
            events.append(MarketEvent(channel=channel, payload=payload, tick=tick, event_type=event_type))

        if not events and isinstance(decoded, Mapping):
            channel = self._normalise_channel(decoded.get("channel") or decoded.get("topic") or decoded.get("type"))
            event_type = self._infer_event_type(channel, decoded)
            events.append(MarketEvent(channel=channel, payload=decoded, tick=None, event_type=event_type))
        return events

    def _expand_message(self, decoded: Any) -> List[Tuple[Optional[str], Mapping[str, Any]]]:
        events: List[Tuple[Optional[str], Mapping[str, Any]]] = []
        if isinstance(decoded, Mapping):
            channel = self._normalise_channel(
                decoded.get("channel") or decoded.get("topic") or decoded.get("type") or decoded.get("event")
            )
            data_payload = decoded.get("data")
            if isinstance(data_payload, Mapping):
                events.append((channel, data_payload))
            elif isinstance(data_payload, list):
                for entry in data_payload:
                    if isinstance(entry, Mapping):
                        events.append((channel, entry))
            else:
                events.append((channel, decoded))
        elif isinstance(decoded, list):
            for entry in decoded:
                events.extend(self._expand_message(entry))
        return events

    def _normalise_channel(self, channel: Any) -> Optional[str]:
        if isinstance(channel, str) and channel.strip():
            return channel.strip().lower()
        return None

    def _infer_event_type(self, channel: Optional[str], payload: Mapping[str, Any]) -> str:
        lower_channel = (channel or "").lower()
        if lower_channel in {"books", "book", "quotes", "orderbook", "depth"}:
            return "orderbook"
        if lower_channel in {"trades", "trade"}:
            return "trade"

        keys = {str(key).lower() for key in payload.keys()}
        price_keys = {"bid_price", "bestbidprice", "ask_price", "bestaskprice"}
        volume_keys = {"bid_volume", "bestbidvolume", "ask_volume", "bestaskvolume"}
        if price_keys & keys and volume_keys & keys:
            return "orderbook"
        if {"price", "volume"} <= keys or {"last_price", "totalvolume"} <= keys:
            return "orderbook"
        if {"trade_price", "trade_volume", "deal_price", "deal_volume"} & keys:
            return "trade"
        if {"side", "price", "quantity"} <= keys and lower_channel == "":
            return "trade"
        if "price" in keys and "quantity" not in keys:
            return "orderbook"
        return "other"

