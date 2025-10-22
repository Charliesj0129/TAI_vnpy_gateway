"""
Order and trade helpers for the Fubon SDK.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Mapping, Optional

from .exceptions import FubonSDKMethodNotFoundError
from .mappings import (
    DIRECTION_MAP,
    DIRECTION_REVERSE_MAP,
    ORDER_STATUS_MAP,
    ORDER_TYPE_MAP,
    ORDER_TYPE_REVERSE_MAP,
)
from .vnpy_compat import Direction, Exchange, Offset, OrderData, OrderRequest, OrderStatus, TradeData

PLACE_ORDER_METHODS = ("place_order", "insert_order", "Order")
CANCEL_ORDER_METHODS = ("cancel_order", "CancelOrder", "delete_order")
QUERY_ORDER_METHODS = ("query_orders", "get_orders", "QueryOrderList")
QUERY_TRADE_METHODS = ("query_deals", "get_deals", "QueryMatch")

LOGGER = logging.getLogger("vnpy_fubon.order")


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


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _normalize_exchange(raw: Any) -> Exchange:
    if isinstance(raw, Exchange):
        return raw
    if isinstance(raw, str):
        upper = raw.upper()
        if upper in {"TWSE", "TW"}:
            return getattr(Exchange, "TWSE", Exchange("TWSE"))  # type: ignore[arg-type]
        if upper in {"TPEX", "OTC"}:
            return getattr(Exchange, "TPEx", Exchange("TPEx"))  # type: ignore[arg-type]
        try:
            return Exchange(upper)  # type: ignore[arg-type]
        except Exception:
            pass
    return getattr(Exchange, "UNKNOWN", Exchange("UNKNOWN"))  # type: ignore[arg-type]


class OrderAPI:
    """
    Encapsulates trading operations with payload/response conversion for vn.py.
    """

    def __init__(
        self,
        client: Any,
        *,
        account_id: Optional[str] = None,
        gateway_name: str = "Fubon",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.client = client
        self.account_id = account_id
        self.gateway_name = gateway_name
        self.logger = logger or LOGGER

    def place_order(
        self,
        request: OrderRequest | Mapping[str, Any],
        extra_payload: Optional[Mapping[str, Any]] = None,
    ) -> OrderData:
        method = self._resolve_method(PLACE_ORDER_METHODS)
        payload = self._build_order_payload(request)
        if extra_payload:
            payload.update(extra_payload)

        self.logger.debug("Placing order with payload %s", payload)
        response = method(**payload)
        order_data = self._to_order_data(response, payload)
        self.logger.info("Order placed: %s (raw=%s)", order_data, response)
        return order_data

    def cancel_order(self, order_id: str, **kwargs: Any) -> bool:
        method = self._resolve_method(CANCEL_ORDER_METHODS)
        payload = {"order_id": order_id}
        payload.update(kwargs)
        self.logger.debug("Cancelling order %s with payload %s", order_id, payload)
        response = method(**payload)
        if isinstance(response, Mapping):
            result = bool(response.get("result") or response.get("success") or response.get("status") == 0)
        else:
            result = bool(response)
        self.logger.info("Cancel result for %s -> %s (raw=%s)", order_id, result, response)
        return result

    def query_open_orders(self, **kwargs: Any) -> List[OrderData]:
        method = self._resolve_method(QUERY_ORDER_METHODS)
        response = method(**kwargs)
        orders: List[OrderData] = []
        if isinstance(response, Mapping):
            response = [response]
        if isinstance(response, Iterable):
            for entry in response:
                if isinstance(entry, Mapping):
                    orders.append(self._to_order_data(entry, {}))
        return orders

    def query_trades(self, **kwargs: Any) -> List[TradeData]:
        method = self._resolve_method(QUERY_TRADE_METHODS)
        response = method(**kwargs)
        trades: List[TradeData] = []
        entries: Iterable[Mapping[str, Any]]
        if isinstance(response, Mapping):
            entries = [response]
        elif isinstance(response, Iterable):
            entries = [item for item in response if isinstance(item, Mapping)]
        else:
            entries = []
        for entry in entries:
            trades.append(self._to_trade_data(entry))
        return trades

    def _resolve_method(self, candidates: Iterable[str]) -> Any:
        for name in candidates:
            method = getattr(self.client, name, None)
            if callable(method):
                return method
        raise FubonSDKMethodNotFoundError(
            f"OrderAPI cannot find any of {candidates} on client {type(self.client).__name__}"
        )

    def _build_order_payload(self, request: OrderRequest | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(request, OrderRequest):
            symbol = request.symbol
            exchange = request.exchange
            direction = request.direction
            order_type = ORDER_TYPE_REVERSE_MAP.get(request.type, "ROD")
            price = request.price
            volume = request.volume
        elif isinstance(request, Mapping):
            symbol = str(request.get("symbol"))
            exchange = request.get("exchange")
            direction = request.get("direction")
            order_type = request.get("order_type") or request.get("type") or "ROD"
            price = request.get("price")
            volume = request.get("volume") or request.get("quantity")
        else:
            raise TypeError(f"Unsupported order request type {type(request)}")

        direction_code = None
        if isinstance(direction, Direction):
            direction_code = DIRECTION_REVERSE_MAP.get(direction)
        elif isinstance(direction, str):
            direction_code = DIRECTION_REVERSE_MAP.get(
                getattr(Direction, direction.upper(), Direction.LONG)
            )

        payload = {
            "symbol": symbol,
            "exchange": str(exchange) if exchange else None,
            "side": direction_code or "BUY",
            "order_type": order_type,
            "price": float(price) if price is not None else None,
            "quantity": float(volume) if volume is not None else None,
        }
        if self.account_id:
            payload.setdefault("account_id", self.account_id)
        return {key: value for key, value in payload.items() if value is not None}

    def _to_order_data(self, raw: Any, request_payload: Mapping[str, Any]) -> OrderData:
        if not isinstance(raw, Mapping):
            order_id = str(request_payload.get("order_id") or request_payload.get("symbol") or "UNKNOWN")
            status = getattr(OrderStatus, "SUBMITTING", OrderStatus.NOTTRADED)
            return OrderData(
                symbol=str(request_payload.get("symbol") or ""),
                exchange=_normalize_exchange(request_payload.get("exchange")),
                orderid=order_id,
                direction=DIRECTION_MAP.get(str(request_payload.get("side")).upper(), Direction.LONG),
                offset=getattr(Offset, "NONE", Offset("none")),  # type: ignore[arg-type]
                price=_ensure_decimal(request_payload.get("price")),
                volume=_ensure_decimal(request_payload.get("quantity")),
                traded=Decimal("0"),
                status=status,
                datetime=datetime.now(timezone.utc),
                gateway_name=self.gateway_name,
                reference=None,
            )

        order_id = str(
            raw.get("order_id")
            or raw.get("ord_no")
            or raw.get("orderid")
            or request_payload.get("order_id")
            or "UNKNOWN"
        )
        status_code = str(raw.get("status") or raw.get("order_status") or raw.get("code") or "").upper()
        status = ORDER_STATUS_MAP.get(status_code)
        if status is None:
            fallback_status = getattr(OrderStatus, "SUBMITTING", OrderStatus.NOTTRADED)
            self.logger.debug("Unknown order status %s; using %s", status_code, fallback_status)
            status = fallback_status
        traded = _ensure_decimal(raw.get("filled_qty") or raw.get("filled") or raw.get("deal_qty") or 0)
        price = _ensure_decimal(raw.get("price") or request_payload.get("price"))
        quantity = _ensure_decimal(raw.get("quantity") or raw.get("qty") or request_payload.get("quantity") or 0)
        direction_code = str(raw.get("side") or raw.get("direction") or "BUY").upper()
        direction = DIRECTION_MAP.get(direction_code, Direction.LONG)
        if direction_code not in DIRECTION_MAP:
            self.logger.debug("Unknown order direction %s; defaulting to %s", direction_code, direction)

        order_data = OrderData(
            symbol=str(raw.get("symbol") or request_payload.get("symbol") or ""),
            exchange=_normalize_exchange(raw.get("exchange") or request_payload.get("exchange")),
            orderid=order_id,
            direction=direction,
            offset=getattr(Offset, "NONE", Offset("none")),  # type: ignore[arg-type]
            price=price,
            volume=quantity,
            traded=traded,
            status=status,
            datetime=_parse_timestamp(raw.get("timestamp") or raw.get("update_time")),
            gateway_name=self.gateway_name,
            reference=str(raw.get("message") or raw.get("note") or ""),
        )
        self.logger.debug("Mapped order payload %s to %s", raw, order_data)
        return order_data

    def _to_trade_data(self, raw: Mapping[str, Any]) -> TradeData:
        trade_id = str(raw.get("trade_id") or raw.get("deal_id") or raw.get("id") or "")
        order_id = str(raw.get("order_id") or raw.get("ord_no") or raw.get("orderid") or "")
        price = _ensure_decimal(raw.get("price"))
        volume = _ensure_decimal(raw.get("quantity") or raw.get("qty") or raw.get("volume") or 0)
        direction_code = str(raw.get("side") or raw.get("direction") or "BUY").upper()
        direction = DIRECTION_MAP.get(direction_code, Direction.LONG)
        if direction_code not in DIRECTION_MAP:
            self.logger.debug("Unknown trade direction %s; defaulting to %s", direction_code, direction)

        trade = TradeData(
            symbol=str(raw.get("symbol") or ""),
            exchange=_normalize_exchange(raw.get("exchange")),
            tradeid=trade_id,
            orderid=order_id,
            direction=direction,
            offset=getattr(Offset, "NONE", Offset("none")),  # type: ignore[arg-type]
            price=price,
            volume=volume,
            datetime=_parse_timestamp(raw.get("timestamp") or raw.get("trade_time")),
            gateway_name=self.gateway_name,
        )
        self.logger.debug("Mapped trade payload %s to %s", raw, trade)
        return trade

    # ------------------------------------------------------------------
    # Public helpers

    def to_order_data(self, raw: Mapping[str, Any], request_payload: Optional[Mapping[str, Any]] = None) -> OrderData:
        """
        Convert a raw SDK order payload into vn.py OrderData.
        """

        return self._to_order_data(raw, request_payload or {})

    def to_trade_data(self, raw: Mapping[str, Any]) -> TradeData:
        """
        Convert a raw SDK trade payload into vn.py TradeData.
        """

        return self._to_trade_data(raw)

