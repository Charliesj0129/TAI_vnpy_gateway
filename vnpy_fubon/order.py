"""
Order and trade helpers for the Fubon SDK.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .exceptions import FubonSDKMethodNotFoundError
from .mappings import (
    DIRECTION_MAP,
    DIRECTION_REVERSE_MAP,
    ORDER_STATUS_MAP,
    ORDER_TYPE_MAP,
    ORDER_TYPE_REVERSE_MAP,
)
from .vnpy_compat import Direction, Exchange, Offset, OrderData, OrderRequest, OrderStatus, TradeData

try:  # pragma: no cover - optional vendor dependencies
    from fubon_neo.constant import (
        BSAction,
        FutOptMarketType,
        FutOptOrderType,
        FutOptPriceType,
        TimeInForce,
    )
except ImportError:  # pragma: no cover - fallback when SDK unavailable
    BSAction = FutOptMarketType = FutOptOrderType = FutOptPriceType = TimeInForce = None  # type: ignore[assignment]

try:  # pragma: no cover - optional vendor dependencies
    from fubon_neo.sdk import Order as SDKOrder
except ImportError:  # pragma: no cover - fallback when SDK unavailable
    SDKOrder = None  # type: ignore[assignment]

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
        account_lookup: Optional[Mapping[str, Any]] = None,
        gateway_name: str = "Fubon",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.client = client
        self.account_id = account_id
        self.gateway_name = gateway_name
        self.logger = logger or LOGGER
        self.account_lookup: Dict[str, Any] = {
            str(key): value for key, value in (account_lookup or {}).items()
        }

    def set_account_lookup(self, accounts: Mapping[str, Any]) -> None:
        """
        Register SDK account objects so downstream calls can provide them to the SDK.
        """

        self.account_lookup = {str(key): value for key, value in accounts.items()}

    def place_order(
        self,
        request: OrderRequest | Mapping[str, Any],
        extra_payload: Optional[Mapping[str, Any]] = None,
    ) -> OrderData:
        payload = self._build_order_payload(request)
        if extra_payload:
            payload.update(extra_payload)

        futopt = getattr(self.client, "futopt", None)
        futopt_place = getattr(futopt, "place_order", None) if futopt else None
        if callable(futopt_place):
            order = self._place_order_via_futopt(futopt_place, payload)
            if order is not None:
                return order

        method = self._resolve_method(PLACE_ORDER_METHODS)

        self.logger.debug("Placing order with payload %s", payload)
        response = method(**payload)
        order_payload = self._unwrap_order_response(response)
        order_data = self._to_order_data(order_payload, payload)
        self.logger.info("Order placed: %s (raw=%s)", order_data, response)
        return order_data

    def cancel_order(self, order_id: str, **kwargs: Any) -> bool:
        futopt = getattr(self.client, "futopt", None)
        futopt_cancel = getattr(futopt, "cancel_order", None) if futopt else None
        if callable(futopt_cancel):
            bridge_result = self._cancel_order_via_futopt(futopt_cancel, order_id, dict(kwargs))
            if bridge_result is not None:
                return bridge_result

        method = self._resolve_method(CANCEL_ORDER_METHODS)
        payload = {"order_id": order_id}
        payload.update(kwargs)
        self.logger.debug("Cancelling order %s with payload %s", order_id, payload)
        response = method(**payload)
        result = self._interpret_response_success(response)
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

    def _place_order_via_futopt(self, place_method: Any, payload: Mapping[str, Any]) -> Optional[OrderData]:
        order_args = self._build_futopt_order_args(payload)
        if order_args is None:
            return None

        account_obj = self._get_sdk_account(payload.get("account"), payload.get("account_id"))
        if account_obj is None:
            account_obj = self._get_sdk_account()
        if account_obj is None:
            self.logger.debug(
                "Unable to resolve account object for place_order %s; falling back to legacy signature.",
                payload,
            )
            return None

        order_obj = order_args["order"]
        unblock = order_args.get("unblock")
        try:
            if unblock is not None:
                response = place_method(account_obj, order_obj, unblock=unblock)
            else:
                response = place_method(account_obj, order_obj)
        except TypeError:
            try:
                if unblock is not None:
                    response = place_method(account_obj, order_obj, unblock)
                else:
                    response = place_method(account_obj, order_obj)
            except Exception as exc:
                self.logger.debug(
                    "FutOpt place_order invocation failed for %s: %s",
                    payload,
                    exc,
                    exc_info=True,
                )
                return None
        except Exception as exc:
            self.logger.warning("FutOpt place_order raised for payload %s: %s", payload, exc)
            raise

        order_payload = self._unwrap_order_response(response)
        order_data = self._to_order_data(order_payload, payload)
        self.logger.info("Order placed via FutOpt: %s (raw=%s)", order_data, response)
        return order_data

    def _cancel_order_via_futopt(
        self,
        cancel_method: Any,
        order_id: str,
        params: Mapping[str, Any],
    ) -> Optional[bool]:
        account_obj = self._get_sdk_account(params.get("account"), params.get("account_id"))
        if account_obj is None:
            account_obj = self._get_sdk_account()
        if account_obj is None:
            self.logger.debug(
                "Unable to resolve account object for cancel_order %s; falling back to order_id signature.",
                order_id,
            )
            return None

        order_result = params.get("order_result")
        if order_result is None:
            futopt = getattr(cancel_method, "__self__", None)
            order_result = self._find_sdk_order_result_by_id(futopt, account_obj, order_id, params)

        if order_result is None:
            self.logger.debug(
                "FutOpt order_result not found for cancel_order %s; falling back to order_id signature.",
                order_id,
            )
            return None

        unblock = params.get("unblock")
        try:
            if unblock is not None:
                response = cancel_method(account_obj, order_result, unblock=unblock)
            else:
                response = cancel_method(account_obj, order_result)
        except TypeError:
            try:
                if unblock is not None:
                    response = cancel_method(account_obj, order_result, unblock)
                else:
                    response = cancel_method(account_obj, order_result)
            except Exception as exc:
                self.logger.debug(
                    "FutOpt cancel_order invocation failed for %s: %s",
                    order_id,
                    exc,
                    exc_info=True,
                )
                return None
        except Exception as exc:
            self.logger.warning("FutOpt cancel_order raised for %s: %s", order_id, exc)
            raise

        result = self._interpret_response_success(response)
        self.logger.info("Cancel result for %s -> %s (raw=%s)", order_id, result, response)
        return result

    def _build_futopt_order_args(self, payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        if SDKOrder is None:
            return None

        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            return None

        quantity_raw = payload.get("quantity") or payload.get("volume")
        try:
            quantity = int(Decimal(str(quantity_raw or 0)))
        except (InvalidOperation, ValueError):
            quantity = 0
        if quantity <= 0:
            self.logger.debug("Invalid quantity for FutOpt order payload %s", payload)
            return None

        price_raw = payload.get("price")
        price = None
        if price_raw not in (None, ""):
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                price = None

        side = payload.get("side") or payload.get("direction")
        bs_action = self._map_bs_action(side)
        if bs_action is None and BSAction is not None:
            bs_action = getattr(BSAction, "Buy") if price is not None else getattr(BSAction, "Sell")

        order_type = self._map_order_type(payload)
        price_type = self._map_price_type(payload, price)
        time_in_force = self._map_time_in_force(payload)
        market_type = self._map_market_type(payload, symbol)
        user_def = payload.get("user_def")
        unblock = payload.get("unblock")

        if bs_action is None:
            self.logger.debug("Unable to determine BSAction for FutOpt order payload %s", payload)
            return None

        order_obj = SDKOrder(
            bs_action,
            symbol,
            quantity,
            market_type,
            price_type,
            time_in_force,
            order_type,
            price=price,
            user_def=user_def,
        )
        return {"order": order_obj, "unblock": unblock}

    def _map_bs_action(self, side: Any) -> Any:
        if BSAction is None:
            return side
        if isinstance(side, BSAction):
            return side
        text = str(side or "").strip()
        if not text and hasattr(BSAction, "UnDefined"):
            return getattr(BSAction, "UnDefined")
        if not text:
            return None
        upper = text.upper()
        if upper in {"BUY", "LONG"}:
            return getattr(BSAction, "Buy", None)
        if upper in {"SELL", "SHORT"}:
            return getattr(BSAction, "Sell", None)
        return self._enum_member(BSAction, text)

    def _map_time_in_force(self, payload: Mapping[str, Any]) -> Any:
        code = payload.get("time_in_force") or payload.get("tif") or payload.get("order_type")
        if TimeInForce is None:
            return code or "ROD"
        candidate = self._enum_member(TimeInForce, code)
        if candidate:
            return candidate
        return getattr(TimeInForce, "ROD", code or "ROD")

    def _map_price_type(self, payload: Mapping[str, Any], price: Optional[float]) -> Any:
        code = payload.get("price_type")
        if code is None and price is None:
            code = "Market"
        elif code is None:
            code = "Limit"
        if FutOptPriceType is None:
            return code
        candidate = self._enum_member(FutOptPriceType, code)
        if candidate:
            return candidate
        default_attr = "Market" if price is None else "Limit"
        return getattr(FutOptPriceType, default_attr, code)

    def _map_order_type(self, payload: Mapping[str, Any]) -> Any:
        code = payload.get("futopt_order_type") or payload.get("order_type")
        offset = payload.get("offset")
        if FutOptOrderType is None:
            if isinstance(offset, str) and offset.lower() in {"close", "closetoday", "closeyesterday"}:
                return "Close"
            return code or "New"
        candidate = self._enum_member(FutOptOrderType, code)
        if candidate:
            return candidate
        if isinstance(offset, str):
            lower_offset = offset.lower()
            if lower_offset in {"close", "closetoday", "closeyesterday"}:
                return getattr(FutOptOrderType, "Close", None) or getattr(FutOptOrderType, "UnDefined", None)
        return getattr(FutOptOrderType, "New", None) or code

    def _map_market_type(self, payload: Mapping[str, Any], symbol: str) -> Any:
        code = payload.get("market_type") or payload.get("marketType")
        if FutOptMarketType is None:
            return code or "Future"
        candidate = self._enum_member(FutOptMarketType, code)
        if candidate:
            return candidate

        # Heuristic based on symbol prefix for options (commonly contain alphabetic suffix)
        if symbol.upper().startswith(("TXO", "TFO", "XO", "O")):
            return getattr(FutOptMarketType, "Option", None)
        return getattr(FutOptMarketType, "Future", None)

    def _enum_member(self, enum_cls: Any, value: Any) -> Optional[Any]:
        if enum_cls is None or value is None:
            return None
        if isinstance(value, enum_cls):
            return value
        text = str(value).strip()
        if not text:
            return None
        lower = text.lower()
        for attr in dir(enum_cls):
            if attr.startswith("_"):
                continue
            member = getattr(enum_cls, attr)
            if not isinstance(member, enum_cls):
                continue
            if attr.lower() == lower:
                return member
            member_name = str(member).split(".")[-1].lower()
            if member_name == lower:
                return member
        return None

    def _unwrap_order_response(self, response: Any) -> Any:
        if isinstance(response, Mapping):
            return response
        data_attr = getattr(response, "data", None)
        if data_attr is not None:
            if isinstance(data_attr, Mapping):
                return data_attr
            if isinstance(data_attr, (list, tuple)) and data_attr:
                first = data_attr[0]
                if isinstance(first, Mapping):
                    return first
        return response

    def _get_sdk_account(self, account: Any = None, account_id: Optional[str] = None) -> Optional[Any]:
        if account is not None:
            return account
        candidate_id = account_id or self.account_id
        if candidate_id:
            candidate = self.account_lookup.get(str(candidate_id))
            if candidate is not None:
                return candidate
        if len(self.account_lookup) == 1:
            return next(iter(self.account_lookup.values()))
        return None

    def _find_sdk_order_result_by_id(
        self,
        futopt: Any,
        account_obj: Any,
        order_id: str,
        params: Mapping[str, Any],
    ) -> Optional[Any]:
        if futopt is None:
            return None
        getter = getattr(futopt, "get_order_results", None)
        if not callable(getter):
            return None

        market_type = params.get("market_type") or params.get("marketType")
        try:
            if market_type is not None:
                response = getter(account_obj, market_type=market_type)
            else:
                response = getter(account_obj)
        except TypeError:
            try:
                response = getter(account_obj, market_type)
            except Exception:
                response = getter(account_obj)
        except Exception as exc:
            self.logger.debug(
                "get_order_results failed for account %s while cancelling order %s: %s",
                account_obj,
                order_id,
                exc,
                exc_info=True,
            )
            return None

        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None

        for entry in self._extract_order_entries(response):
            entry_id = self._extract_order_id(entry)
            if entry_id and entry_id == order_id_str:
                return entry
        return None

    def _extract_order_entries(self, payload: Any) -> List[Any]:
        if payload is None:
            return []

        if isinstance(payload, Mapping):
            for key in ("data", "orders", "result", "results"):
                nested = payload.get(key)
                if nested is not None:
                    return self._extract_order_entries(nested)
            return [payload]

        data_attr = getattr(payload, "data", None)
        if data_attr is not None:
            return self._extract_order_entries(data_attr)

        if isinstance(payload, (list, tuple, set)):
            entries: List[Any] = []
            for item in payload:
                entries.extend(self._extract_order_entries(item))
            return entries

        return [payload]

    def _extract_order_id(self, entry: Any) -> Optional[str]:
        if entry is None:
            return None

        candidates = (
            "order_id",
            "orderId",
            "orderid",
            "ord_no",
            "ordNo",
            "order_no",
            "orderNo",
            "seq_no",
            "seqNo",
            "id",
        )

        if isinstance(entry, Mapping):
            for key in candidates:
                if key in entry and entry[key] not in (None, ""):
                    return str(entry[key]).strip()

        for key in candidates:
            try:
                value = getattr(entry, key)
            except Exception:
                continue
            if value not in (None, ""):
                return str(value).strip()

        return None

    def _interpret_response_success(self, response: Any) -> bool:
        if isinstance(response, Mapping):
            for key in ("success", "is_success", "result", "status", "code"):
                if key not in response:
                    continue
                success = self._normalize_success_value(response[key])
                if success is not None:
                    return success
            return bool(response)

        for attr in ("is_success", "success", "result", "status", "code"):
            if hasattr(response, attr):
                try:
                    success = self._normalize_success_value(getattr(response, attr))
                except Exception:
                    success = None
                if success is not None:
                    return success

        return bool(response)

    @staticmethod
    def _normalize_success_value(value: Any) -> Optional[bool]:
        if value in (None, "", "null"):
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if value == 0:
                return True
            if value == 1:
                return True
            return None
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                return None
            if text in {"ok", "success", "true", "yes", "y"}:
                return True
            if text in {"fail", "failed", "false", "error", "no", "n"}:
                return False
            if text == "0":
                return True
            if text == "1":
                return True
        return None

    def _build_order_payload(self, request: OrderRequest | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(request, OrderRequest):
            symbol = request.symbol
            exchange = request.exchange
            direction = request.direction
            order_type = ORDER_TYPE_REVERSE_MAP.get(request.type, "ROD")
            price = request.price
            volume = request.volume
            offset = request.offset
        elif isinstance(request, Mapping):
            symbol = str(request.get("symbol"))
            exchange = request.get("exchange")
            direction = request.get("direction")
            order_type = request.get("order_type") or request.get("type") or "ROD"
            price = request.get("price")
            volume = request.get("volume") or request.get("quantity")
            offset = request.get("offset")
        else:
            raise TypeError(f"Unsupported order request type {type(request)}")

        direction_code = None
        if isinstance(direction, Direction):
            direction_code = DIRECTION_REVERSE_MAP.get(direction)
        elif isinstance(direction, str):
            direction_code = DIRECTION_REVERSE_MAP.get(
                getattr(Direction, direction.upper(), Direction.LONG)
            )

        if isinstance(offset, Offset):
            offset_code = str(offset).split(".")[-1]
        elif isinstance(offset, str):
            offset_code = offset.split(".")[-1]
        else:
            offset_code = None

        payload = {
            "symbol": symbol,
            "exchange": str(exchange) if exchange else None,
            "side": direction_code or "BUY",
            "order_type": order_type,
            "price": float(price) if price is not None else None,
            "quantity": float(volume) if volume is not None else None,
            "offset": offset_code,
        }
        if payload.get("price_type") is None:
            payload["price_type"] = "Market" if payload.get("price") is None else "Limit"
        if payload.get("time_in_force") is None:
            payload["time_in_force"] = "ROD"
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

