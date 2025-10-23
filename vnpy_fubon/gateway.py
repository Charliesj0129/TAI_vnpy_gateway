"""
Gateway implementation that aligns with vn.py's BaseGateway interface and
manages account, order, and market data flows for the Fubon Securities SDK.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from threading import Timer
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from adapters.fubon_to_vnpy import MarketEnvelopeNormalizer
from .account import AccountAPI
from .fubon_connect import FubonAPIConnector, create_authenticated_client
from .logging_config import configure_logging
from .market import MarketAPI
from .order import OrderAPI
from .normalization import normalize_exchange, normalize_product, normalize_symbol
from .vnpy_compat import (
    AccountData,
    BaseGateway,
    Event,
    EVENT_CONTRACT,
    EVENT_ACCOUNT,
    EVENT_LOG,
    EVENT_FUBON_MARKET_RAW,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
    LogData,
    ClosePositionRecord,
    BarData,
    ContractData,
    EstimateMarginData,
    OrderData,
    OrderRequest,
    PositionData,
    EquityData,
    Exchange,
    Product,
    SubscribeRequest,
    TickData,
    TradeData,
    OptionType,
    HistoryRequest,
    Interval,
)

try:  # pragma: no cover - optional SDK dependency
    from fubon_neo.sdk import Mode
except ImportError:  # pragma: no cover - graceful degradation
    Mode = None  # type: ignore[assignment]

try:  # pragma: no cover - optional SDK dependency
    from fubon_neo.constant import CallPut
except ImportError:  # pragma: no cover - graceful degradation
    CallPut = None  # type: ignore[assignment]

class FubonGateway(BaseGateway):
    """
    vn.py-compatible gateway for the Fubon Securities SDK.
    """

    default_name = "FUBON"
    exchanges: List[Exchange] = []

    def __init__(
        self,
        event_engine: Any,
        gateway_name: str = default_name,
        *,
        connector: Optional[FubonAPIConnector] = None,
        client: Any = None,
        log_level: Optional[int] = None,
        **_: Any,
    ) -> None:
        super().__init__(event_engine, gateway_name)
        resolved_log_level = log_level if log_level is not None else logging.INFO
        self.logger = configure_logging(
            log_level=resolved_log_level,
            logger_name="vnpy_fubon.gateway",
            gateway_name=gateway_name,
        )
        self.connector = connector
        self.client = client
        self.login_response: Any = None

        self.accounts: list[Any] = []
        self.primary_account: Any = None
        self.primary_account_id: Optional[str] = None
        self.account_map: dict[str, Any] = {}
        self.account_metadata: dict[str, Mapping[str, Any]] = {}
        self.contracts: Dict[str, ContractData] = {}
        self._symbol_aliases: Dict[str, str] = {}
        self._symbol_exchange_aliases: Dict[Tuple[str, str], str] = {}
        self._default_exchange_code = os.getenv("FUBON_EXCHANGE", "TAIFEX")

        self.account_api: Optional[AccountAPI] = None
        self.order_api: Optional[OrderAPI] = None
        self.market_api: Optional[MarketAPI] = None

        # Websocket state
        self._ws_lock = threading.RLock()
        self._ws_client: Any = None
        self._ws_connected = False
        self._ws_handlers_registered = False
        self._ws_registered_events: list[Tuple[str, Callable[..., None]]] = []
        self._active_subscriptions: Set[Tuple[str, str, Optional[bool]]] = set()
        self._subscription_ids_by_key: Dict[Tuple[str, str, Optional[bool]], str] = {}
        self._subscription_key_by_id: Dict[str, Tuple[str, str, Optional[bool]]] = {}
        self._ws_reconnect_attempts = 0
        self._ws_reconnect_timer: Optional[Timer] = None
        self._ws_ping_timer: Optional[Timer] = None
        self._ws_ping_interval = int(os.getenv("FUBON_WS_PING_INTERVAL", "30"))
        self._closing = False
        self._ws_sdk_callbacks: list[Tuple[str, Callable[..., None]]] = []
        self._token_timer: Optional[Timer] = None
        self._token_refresh_interval = 900  # seconds; aligns with 15-minute default heartbeat
        self._market_normalizer: Optional[MarketEnvelopeNormalizer] = None
        self._preferred_ws_mode = os.getenv("FUBON_REALTIME_MODE", "Normal")
        self._normal_mode_warning_emitted = False
        self._subscription_warning_emitted = False

    # ----------------------------------------------------------------------
    # vn.py BaseGateway interface

    def connect(self, setting: Optional[Mapping[str, Any]] = None) -> None:
        """
        Establish SDK session and initialise helper APIs.
        """

        self.write_log("Connecting to Fubon gateway...", state="connecting")
        if self.client is None:
            if self.connector is not None:
                self.client, self.login_response = self.connector.connect()
            else:
                config_path: Optional[Path] = None
                dotenv_path: Optional[Path] = None
                use_env_only = False
                if setting:
                    config_value = setting.get("config_path")
                    if config_value:
                        config_path = Path(config_value)
                    dotenv_value = setting.get("dotenv_path")
                    if dotenv_value:
                        dotenv_path = Path(dotenv_value)
                    use_env_only = bool(setting.get("use_env_only"))
                self.client, self.login_response = create_authenticated_client(
                    config_path=None if use_env_only else config_path,
                    dotenv_path=dotenv_path,
                    log_level=self.logger.level,
                )
        else:
            self.login_response = getattr(self.client, "login_response", None)

        if self.client is None:
            raise RuntimeError("Failed to acquire Fubon SDK client.")

        self._populate_account_metadata(setting)

        self.account_api = AccountAPI(self.client, gateway_name=self.gateway_name, logger=self.logger)
        self.order_api = OrderAPI(
            self.client,
            account_id=self.primary_account_id,
            account_lookup=self.account_map,
            gateway_name=self.gateway_name,
            logger=self.logger,
        )
        if self.primary_account_id:
            self.order_api.account_id = self.primary_account_id
        if self.order_api:
            self.order_api.set_account_lookup(self.account_map)
        self.market_api = MarketAPI(self.client, gateway_name=self.gateway_name, logger=self.logger)

        self._register_order_callbacks()
        self._prepare_realtime()
        self._ensure_websocket_client(register_handler=True)
        self._start_token_refresh()
        self._load_and_publish_contracts()

        self.write_log("Fubon gateway connected.", state="connected")

    def close(self) -> None:
        """
        Disconnect websocket client and reset state.
        """

        self._closing = True
        self.write_log("Closing Fubon gateway...", state="closing")
        self._cancel_ws_reconnect()
        self._disconnect_websocket()
        self.accounts.clear()
        self.primary_account = None
        self.primary_account_id = None
        self.account_api = None
        self.order_api = None
        self.market_api = None
        self.contracts.clear()
        self._symbol_aliases.clear()
        self._symbol_exchange_aliases.clear()
        self._subscription_ids_by_key.clear()
        self._subscription_key_by_id.clear()
        self.write_log("Fubon gateway closed.", state="closed")
        self._closing = False

    def subscribe(self, req: SubscribeRequest, *, channels: Optional[Sequence[str]] = None, after_hours: Optional[bool] = None) -> None:
        self.subscribe_quotes([req.symbol], channels=channels, after_hours=after_hours)

    def subscribe_quotes(
        self,
        symbols: Sequence[str],
        *,
        channels: Optional[Sequence[str]] = None,
        after_hours: Optional[bool] = None,
    ) -> None:
        client = self._ensure_websocket_client(register_handler=True)
        payload_channels = list(channels or ("books",))

        unique_symbols: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip()
            if not symbol:
                continue
            if symbol not in unique_symbols:
                unique_symbols.append(symbol)

        if not unique_symbols:
            return

        requires_normal = any(ch.lower() in {"candles", "candle", "aggregates", "aggregate"} for ch in payload_channels)
        if requires_normal and not self._is_normal_mode() and not self._normal_mode_warning_emitted:
            warning = (
                "SDK realtime mode is not Normal; aggregates/candles subscriptions may be rejected. "
                "Set FUBON_REALTIME_MODE=Normal before connecting."
            )
            self.logger.warning(warning, extra={"gateway_state": "ws_mode_warning"})
            self._put_event(EVENT_LOG, warning)
            self._normal_mode_warning_emitted = True

        for channel in payload_channels:
            pending_symbols = [
                symbol
                for symbol in unique_symbols
                if (channel, symbol, after_hours) not in self._active_subscriptions
            ]
            if not pending_symbols:
                continue

            projected_total = len(self._active_subscriptions) + len(pending_symbols)
            if projected_total > 200 and not self._subscription_warning_emitted:
                message = (
                    f"Websocket subscriptions approaching vendor limit (current={len(self._active_subscriptions)}, "
                    f"projected={projected_total}). Consider reducing channels per connection."
                )
                self.logger.warning(message, extra={"gateway_state": "subscription_warning"})
                self._put_event(EVENT_LOG, message)
                self._subscription_warning_emitted = True

            batch_completed = False
            if len(pending_symbols) > 1:
                batch_message = {"channel": channel, "symbols": pending_symbols}
                if after_hours is not None:
                    batch_message["afterHours"] = bool(after_hours)
                try:
                    response = client.subscribe(batch_message)
                except TypeError:
                    self.logger.debug(
                        "Batch subscribe unsupported for payload %s; falling back to per-symbol requests.",
                        batch_message,
                        exc_info=True,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Batch subscribe failed for %s: %s",
                        batch_message,
                        exc,
                        extra={
                            "channel": channel,
                            "gateway_state": "subscribe_failed",
                        },
                    )
                    if self._should_reconnect_after_error(exc):
                        self._schedule_ws_reconnect()
                    else:
                        self._put_event(EVENT_LOG, f"Subscription rejected for {batch_message}: {exc}")
                else:
                    parsed_ids = self._parse_subscription_ids(response, pending_symbols)
                    id_map = {symbol: parsed_ids.get(symbol) for symbol in pending_symbols}
                    self._register_subscriptions(channel, id_map, after_hours)
                    batch_completed = True

            if batch_completed:
                continue

            for symbol in pending_symbols:
                message = {"channel": channel, "symbol": symbol}
                if after_hours is not None:
                    message["afterHours"] = bool(after_hours)
                try:
                    response = client.subscribe(message)
                except Exception as exc:
                    self.logger.warning(
                        "Subscribe failed for %s: %s",
                        message,
                        exc,
                        extra={
                            "channel": channel,
                            "symbol": symbol,
                            "gateway_state": "subscribe_failed",
                        },
                    )
                    if self._should_reconnect_after_error(exc):
                        self._schedule_ws_reconnect()
                    else:
                        self._put_event(EVENT_LOG, f"Subscription rejected for {message}: {exc}")
                    raise
                parsed_ids = self._parse_subscription_ids(response, [symbol])
                id_map = {symbol: parsed_ids.get(symbol)}
                self._register_subscriptions(channel, id_map, after_hours)

    def unsubscribe_quotes(
        self,
        symbols: Sequence[str],
        *,
        channels: Optional[Sequence[str]] = None,
        after_hours: Optional[bool] = None,
    ) -> None:
        if not self._ws_client:
            return
        payload_channels = list(channels or ("books",))
        unique_symbols: list[str] = []
        for raw_symbol in symbols:
            symbol = str(raw_symbol).strip()
            if not symbol:
                continue
            if symbol not in unique_symbols:
                unique_symbols.append(symbol)

        for symbol in unique_symbols:
            for channel in payload_channels:
                candidates: list[Tuple[str, str, Optional[bool]]]
                if after_hours is None:
                    candidates = [entry for entry in self._active_subscriptions if entry[0] == channel and entry[1] == symbol]
                else:
                    candidates = [(channel, symbol, after_hours)]
                for key in candidates:
                    if key not in self._active_subscriptions:
                        continue
                    stored_after_hours = key[2]
                    subscription_id = self._subscription_ids_by_key.get(key)
                    payload_options: list[Mapping[str, Any]] = []
                    if subscription_id:
                        payload_options.append({"ids": [subscription_id]})
                        payload_options.append({"id": subscription_id})
                    message = {"channel": channel, "symbol": symbol}
                    if stored_after_hours is not None:
                        message["afterHours"] = bool(stored_after_hours)
                    payload_options.append(message)

                    success = False
                    used_payload: Optional[Mapping[str, Any]] = None
                    last_exception: Optional[Exception] = None
                    for payload in payload_options:
                        try:
                            self._ws_client.unsubscribe(payload)
                            success = True
                            used_payload = payload
                            break
                        except TypeError as exc:
                            last_exception = exc
                            continue
                        except Exception as exc:  # pragma: no cover - vendor behaviour
                            last_exception = exc
                            self.logger.warning(
                                "Unsubscribe failed: %s",
                                exc,
                                extra={
                                    "channel": channel,
                                    "symbol": symbol,
                                    "gateway_state": "unsubscribe_failed",
                                },
                            )
                            break

                    if success and used_payload is not None:
                        log_payload = dict(used_payload)
                        log_payload.setdefault("channel", channel)
                        log_payload.setdefault("symbol", symbol)
                        self.logger.debug(
                            "Unsubscribed websocket channel %s",
                            log_payload,
                            extra={
                                "channel": channel,
                                "symbol": symbol,
                                "gateway_state": "unsubscribed",
                            },
                        )
                    elif last_exception is not None:
                        self.logger.debug(
                            "All unsubscribe payload variants failed for channel=%s symbol=%s: %s",
                            channel,
                            symbol,
                            last_exception,
                            exc_info=isinstance(last_exception, TypeError),
                        )

                    self._forget_subscription(key)

    def _register_subscriptions(
        self,
        channel: str,
        symbol_id_map: Mapping[str, Optional[str]],
        after_hours: Optional[bool],
    ) -> None:
        for raw_symbol, subscription_id in symbol_id_map.items():
            symbol = str(raw_symbol).strip()
            if not symbol:
                continue
            key = (channel, symbol, after_hours)
            existing_id = self._subscription_ids_by_key.get(key)
            if key not in self._active_subscriptions:
                self._active_subscriptions.add(key)
            if subscription_id:
                sub_id = str(subscription_id).strip()
                if sub_id:
                    self._subscription_ids_by_key[key] = sub_id
                    self._subscription_key_by_id[sub_id] = key
            elif existing_id:
                self._subscription_ids_by_key[key] = existing_id

            log_payload: Dict[str, Any] = {"channel": channel, "symbol": symbol}
            if after_hours is not None:
                log_payload["afterHours"] = bool(after_hours)
            if subscription_id:
                log_payload["id"] = subscription_id

            self.logger.debug(
                "Subscribed websocket channel %s",
                log_payload,
                extra={
                    "channel": channel,
                    "symbol": symbol,
                    "gateway_state": "subscribed",
                },
            )

    def _forget_subscription(self, key: Tuple[str, str, Optional[bool]]) -> None:
        subscription_id = self._subscription_ids_by_key.pop(key, None)
        if subscription_id:
            self._subscription_key_by_id.pop(subscription_id, None)
        self._active_subscriptions.discard(key)

    def _parse_subscription_ids(self, response: Any, symbols: Sequence[str]) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        target_symbols = {str(symbol).strip() for symbol in symbols if str(symbol).strip()}

        def _record(symbol: Any, value: Any) -> None:
            if symbol is None or value in (None, "", "null"):
                return
            symbol_str = str(symbol).strip()
            if symbol_str and symbol_str in target_symbols:
                parsed[symbol_str] = str(value).strip()

        def _walk(payload: Any) -> None:
            if payload is None:
                return

            if isinstance(payload, Mapping):
                ids_value = payload.get("ids")
                if isinstance(ids_value, (list, tuple)):
                    for symbol, sub_id in zip(symbols, ids_value):
                        _record(symbol, sub_id)

                id_value = (
                    payload.get("id")
                    or payload.get("subscriptionId")
                    or payload.get("subscription_id")
                    or payload.get("channelId")
                    or payload.get("channel_id")
                )
                symbol_value = (
                    payload.get("symbol")
                    or payload.get("code")
                    or payload.get("target")
                    or payload.get("symbolId")
                )
                if symbol_value is not None:
                    _record(symbol_value, id_value)
                elif id_value is not None and len(target_symbols) == 1:
                    _record(next(iter(target_symbols)), id_value)

                for key in ("data", "result", "results", "responses", "subscriptions"):
                    if key in payload:
                        _walk(payload[key])

                attr_data = getattr(payload, "data", None)
                if attr_data is not None and attr_data is not payload:
                    _walk(attr_data)
                return

            attr_data = getattr(payload, "data", None)
            if attr_data is not None and attr_data is not payload:
                _walk(attr_data)

            if isinstance(payload, (list, tuple, set)):
                for item in payload:
                    _walk(item)

        _walk(response)
        return parsed

    def _get_market_normalizer(self) -> MarketEnvelopeNormalizer:
        if self._market_normalizer is None:
            self._market_normalizer = MarketEnvelopeNormalizer(gateway_name=self.gateway_name)
        return self._market_normalizer

    def _normalize_market_trade(self, payload: Mapping[str, Any]) -> Optional[TradeData]:
        try:
            normalizer = self._get_market_normalizer()
            normalized = normalizer.normalize_trade(payload)
        except Exception as exc:
            self.logger.debug("Failed to normalize trade payload %s: %s", payload, exc)
            return None
        trade = normalized.trade
        trade.gateway_name = self.gateway_name
        trade.extra = getattr(trade, "extra", {}) or {}
        trade.extra["source"] = "market"
        trade.extra["channel"] = normalized.raw.channel
        trade.extra["latency_ms"] = normalized.raw.latency_ms
        return trade

    def _normalize_market_bar(self, payload: Mapping[str, Any]) -> Optional[BarData]:
        source: Mapping[str, Any]
        data_field = payload.get("data")
        if isinstance(data_field, Mapping):
            source = data_field
        else:
            source = payload

        symbol = str(source.get("symbol") or "").strip()
        if not symbol:
            return None

        exchange_code = source.get("exchange") or source.get("market") or self._default_exchange_code
        exchange = normalize_exchange(exchange_code, default=self._default_exchange_code)
        dt = self._parse_ws_datetime(
            source.get("date") or source.get("timestamp") or source.get("time") or source.get("datetime")
        ) or datetime.now(timezone.utc)

        timeframe = source.get("timeframe") or source.get("interval")
        interval: Optional[Interval] = None
        try:
            if isinstance(timeframe, (int, float)):
                if timeframe == 1:
                    interval = getattr(Interval, "MINUTE", None)
                elif timeframe == 5:
                    interval = getattr(Interval, "MINUTE", None)
                elif timeframe in (15, 30):
                    interval = getattr(Interval, "MINUTE", None)
                elif timeframe == 60:
                    interval = getattr(Interval, "HOUR", None)
                elif timeframe >= 1440:
                    interval = getattr(Interval, "DAILY", None)
            elif isinstance(timeframe, str):
                mapping = {
                    "1m": getattr(Interval, "MINUTE", None),
                    "5m": getattr(Interval, "MINUTE", None),
                    "15m": getattr(Interval, "MINUTE", None),
                    "30m": getattr(Interval, "MINUTE", None),
                    "1h": getattr(Interval, "HOUR", None),
                    "1d": getattr(Interval, "DAILY", None),
                    "d": getattr(Interval, "DAILY", None),
                }
                interval = mapping.get(timeframe.lower())
        except Exception:
            interval = getattr(Interval, "MINUTE", None)
        if interval is None:
            interval = getattr(Interval, "MINUTE", None)

        open_price = self._safe_float(source.get("open"))
        high_price = self._safe_float(source.get("high"))
        low_price = self._safe_float(source.get("low"))
        close_price = self._safe_float(source.get("close"))
        volume = self._safe_float(source.get("volume"))
        turnover = self._safe_float(source.get("turnover"))
        open_interest = self._safe_float(source.get("openInterest"))

        bar = BarData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            interval=interval,
            volume=volume,
            turnover=turnover,
            open_interest=open_interest,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
        )
        return bar

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        if value in (None, "", "null"):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _parse_ws_datetime(self, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                return datetime.fromisoformat(text)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
                    try:
                        return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
        return None

    def _resolve_ws_mode(self) -> Optional[Any]:
        mode_value = (self._preferred_ws_mode or "").strip()
        if not mode_value:
            return None
        if Mode is not None:
            for member in Mode:
                if str(member.name).lower() == mode_value.lower():
                    return member
        return mode_value

    def _is_normal_mode(self) -> bool:
        mode = self._preferred_ws_mode
        if Mode is not None and isinstance(mode, Mode):  # type: ignore[arg-type]
            return mode == Mode.Normal
        if isinstance(mode, str):
            return mode.strip().lower() == "normal"
        return True

    def _get_account_object(self, account_id: Optional[str]) -> Optional[Any]:
        if account_id:
            return self.account_map.get(str(account_id))
        return self.primary_account

    def _coerce_call_put(self, value: Any) -> Any:
        if value in (None, "", "null"):
            return None
        if CallPut is None:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"call", "c"}:
                    return "Call"
                if text in {"put", "p"}:
                    return "Put"
            return value
        if isinstance(value, CallPut):  # type: ignore[arg-type]
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            for member in CallPut:  # type: ignore[assignment]
                member_name = getattr(member, "name", str(member)).lower()
                if member_name == text:
                    return member
                member_value = str(member).split(".")[-1].lower()
                if member_value == text:
                    return member
                if text in {"call", "c"} and member_name.startswith("call"):
                    return member
                if text in {"put", "p"} and member_name.startswith("put"):
                    return member
        return value

    def query_account(self) -> Optional[AccountData]:
        if not self.account_api:
            return None
        account = self.account_api.query_account()
        self._put_event(EVENT_ACCOUNT, account)
        self._put_event(EVENT_LOG, f"Account {account.accountid} queried.")
        return account

    def query_equity(self, account_id: Optional[str] = None) -> Sequence[EquityData]:
        if not self.account_api:
            return []
        if account_id:
            account_obj = self.account_map.get(str(account_id))
            if not account_obj:
                self.logger.warning("Account %s not found for equity query.", account_id)
                return []
        else:
            account_obj = self.primary_account
        if not account_obj:
            self.logger.warning("No active account available for equity query.")
            return []
        equities = self.account_api.query_margin_equity(account_obj)
        for equity in equities:
            log_message = (
                f"Equity snapshot for {equity.accountid} ({equity.currency}) - "
                f"today_equity={equity.today_equity} excess_margin={equity.excess_margin}"
            )
            self._put_event(EVENT_LOG, log_message)
        return equities

    def estimate_margin(
        self,
        request: OrderRequest | Mapping[str, Any],
        *,
        account_id: Optional[str] = None,
        extra_payload: Optional[Mapping[str, Any]] = None,
    ) -> EstimateMarginData:
        if not self.order_api:
            raise RuntimeError("Gateway not connected.")
        account_obj = self._get_account_object(account_id)
        if account_obj is None:
            raise RuntimeError("No account context available for margin estimation.")
        result = self.order_api.estimate_margin(account_obj, request, extra_payload)
        self._put_event(
            EVENT_LOG,
            f"Estimated margin for {result.symbol} ({result.currency}) -> {result.estimate_margin}",
        )
        return result

    def modify_order_lot(
        self,
        order_id: str,
        new_lot: int,
        *,
        account_id: Optional[str] = None,
        unblock: Optional[bool] = None,
    ) -> OrderData:
        if not self.order_api:
            raise RuntimeError("Gateway not connected.")
        account_obj = self._get_account_object(account_id)
        if account_obj is None:
            raise RuntimeError("No account context available for modify_lot.")
        result = self.order_api.modify_order_lot(
            order_id,
            new_lot,
            account=account_obj,
            account_id=account_id or self.primary_account_id,
            unblock=unblock,
        )
        self._put_event(EVENT_ORDER, result)
        self.write_log(
            f"Modify lot requested for order {order_id} -> new_lot={new_lot}",
            state="modify_lot",
        )
        return result

    def query_close_position_records(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        *,
        account_id: Optional[str] = None,
    ) -> Sequence[ClosePositionRecord]:
        if not self.account_api:
            return []
        account_obj = self._get_account_object(account_id)
        if account_obj is None:
            self.logger.warning("No active account available for close position query.")
            return []
        records = self.account_api.query_close_position_records(account_obj, start_date, end_date)
        for record in records:
            self._put_event(
                EVENT_LOG,
                f"Closed position {record.symbol} {record.direction} volume={record.volume} pnl={record.pnl}",
            )
        return records

    def query_order_history(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        *,
        account_id: Optional[str] = None,
        market_type: Optional[Any] = None,
    ) -> Sequence[OrderData]:
        if not self.order_api:
            return []
        account_obj = self._get_account_object(account_id)
        if account_obj is None:
            self.logger.warning("No active account available for order history query.")
            return []
        orders = self.order_api.query_order_history(
            account_obj,
            start_date,
            end_date,
            market_type=market_type,
            account_id=account_id or self.primary_account_id,
        )
        for order in orders:
            self._put_event(EVENT_ORDER, order)
        return orders

    def convert_symbol(
        self,
        base_symbol: str,
        expiry_date: str,
        *,
        strike_price: Optional[float] = None,
        call_put: Optional[Any] = None,
    ) -> str:
        if self.client is None:
            raise RuntimeError("Gateway client unavailable.")
        futopt = getattr(self.client, "futopt", None)
        method = getattr(futopt, "convert_symbol", None) if futopt else None
        if not callable(method):
            raise FubonSDKMethodNotFoundError(
                f"Client {type(self.client).__name__} does not expose futopt.convert_symbol."
            )

        kwargs: Dict[str, Any] = {}
        if strike_price is not None:
            kwargs["strike_price"] = strike_price
        call_put_value = self._coerce_call_put(call_put)
        if call_put_value is not None:
            kwargs["call_put"] = call_put_value

        result = method(base_symbol, expiry_date, **kwargs)
        if isinstance(result, Mapping):
            symbol = result.get("symbol") or result.get("data")
            if isinstance(symbol, Mapping):
                symbol = symbol.get("symbol")
            if symbol:
                return str(symbol)
        return str(result)

    def query_positions(self) -> Sequence[PositionData]:
        if not self.account_api:
            return []
        account_obj = self._get_account_object(None)
        if account_obj is not None:
            positions = self.account_api.query_positions(account=account_obj)
        else:
            positions = self.account_api.query_positions()
        for position in positions:
            self._put_event(EVENT_POSITION, position)
        return positions

    def place_order(self, request: OrderRequest | Mapping[str, Any]) -> OrderData:
        if not self.order_api:
            raise RuntimeError("Gateway not connected.")
        order = self.order_api.place_order(request)
        self._put_event(EVENT_ORDER, order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        if not self.order_api:
            raise RuntimeError("Gateway not connected.")
        result = self.order_api.cancel_order(order_id)
        if result:
            self.write_log(f"Cancel request for order {order_id} submitted.")
        return result

    def query_trades(self) -> Sequence[TradeData]:
        if not self.order_api:
            return []
        trades = self.order_api.query_trades()
        for trade in trades:
            self._put_event(EVENT_TRADE, trade)
        return trades

    def query_contracts(self) -> Sequence[ContractData]:
        return list(self.contracts.values())

    def query_history(self, request: HistoryRequest) -> Sequence[BarData]:
        """
        Fetch historical bar data for CTA backtesting integrations.
        """

        if request is None:
            return []

        symbol = (request.symbol or "").strip()
        if not symbol:
            self.logger.warning(
                "query_history called without a symbol.",
                extra={"gateway_state": "history_warning"},
            )
            return []

        timeframe, minutes_per_bar = self._resolve_history_timeframe(request.interval)
        if timeframe is None:
            self.logger.warning(
                "Unsupported interval %s for historical data request.",
                request.interval,
                extra={"gateway_state": "history_warning"},
            )
            return []

        normalized_start = self._ensure_utc(request.start)
        normalized_end = self._ensure_utc(request.end)
        limit = self._estimate_history_limit(normalized_start, normalized_end, minutes_per_bar)

        try:
            bars = self.fetch_candles(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:  # pragma: no cover - vendor behaviour
            self.logger.warning(
                "Historical data download failed for %s: %s",
                symbol,
                exc,
                extra={"gateway_state": "history_error"},
            )
            return []

        filtered = self._filter_history_window(bars, normalized_start, normalized_end)
        filtered.sort(key=lambda bar: bar.datetime)
        return filtered

    def get_default_setting(self) -> Mapping[str, Any]:
        """
        Provide default connection fields for vn.py UI integration.
        """

        return {
            "user_id": os.getenv("FUBON_USER_ID", ""),
            "password": os.getenv("FUBON_USER_PASSWORD", ""),
            "ca_path": os.getenv("FUBON_CA_PATH", ""),
            "ca_password": os.getenv("FUBON_CA_PASSWORD", ""),
            "account_id": os.getenv("FUBON_PRIMARY_ACCOUNT", ""),
        }

    def _load_and_publish_contracts(self) -> None:
        records = self._fetch_contracts_from_rest()
        if not records:
            self.logger.warning(
                "Fubon REST API returned no contracts; GUI features may be limited.",
                extra={"gateway_state": "contracts_missing"},
            )
            return

        self.contracts.clear()
        self._symbol_aliases.clear()
        self._symbol_exchange_aliases.clear()

        emitted = 0
        for contract, raw_symbol, raw_exchange in records:
            self.contracts[contract.vt_symbol] = contract
            self._register_contract_aliases(contract, raw_symbol, raw_exchange)
            self.on_contract(contract)
            emitted += 1

        self.write_log(
            f"Loaded {emitted} contracts from Fubon REST API.",
            state="contracts_loaded",
        )

    # ------------------------------------------------------------------
    # Internal helpers

    def _call_rest_with_retry(
        self,
        func: Callable[..., Any],
        *args: Any,
        max_attempts: int = 5,
        base_delay: float = 0.5,
        **kwargs: Any,
    ) -> Any:
        attempt = 0
        delay = base_delay
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                attempt += 1
                if attempt >= max_attempts or not self._is_rate_limit_error(exc):
                    raise
                self.logger.debug(
                    "REST rate limit encountered for %s; retrying in %.2f seconds (attempt %s/%s).",
                    getattr(func, "__name__", repr(func)),
                    delay,
                    attempt,
                    max_attempts,
                )
                time.sleep(delay)
                delay = min(delay * 2, 8.0)

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        status_candidates = (
            getattr(exc, "status_code", None),
            getattr(exc, "status", None),
        )
        for status in status_candidates:
            if status == 429:
                return True

        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            if status == 429:
                return True

        message = str(exc).lower()
        keywords = ("429", "too many requests", "rate limit")
        return any(keyword in message for keyword in keywords)

    def _fetch_contracts_from_rest(self) -> List[Tuple[ContractData, str, str]]:
        intraday = self._get_intraday_client()
        if intraday is None:
            self.logger.debug("REST intraday client unavailable; skipping contract download.")
            return []

        product_metadata = self._fetch_product_metadata(intraday)

        query_params = [
            {"type": "FUTURE", "session": "REGULAR"},
            {"type": "FUTURE", "session": "AFTERHOURS"},
            {"type": "OPTION", "session": "REGULAR"},
            {"type": "OPTION", "session": "AFTERHOURS"},
        ]

        contracts: Dict[str, Tuple[ContractData, str, str]] = {}
        for params in query_params:
            try:
                response = self._call_rest_with_retry(
                    intraday.tickers,
                    exchange=self._default_exchange_code,
                    limit=2000,
                    **params,
                )
            except Exception as exc:  # pragma: no cover - vendor behaviour
                self.logger.debug("intraday.tickers failed for %s: %s", params, exc)
                continue

            for item in response.get("data") or []:
                mapped = self._map_ticker_to_contract(item, product_metadata)
                if not mapped:
                    continue
                contract, raw_symbol, raw_exchange = mapped
                contracts[contract.vt_symbol] = (contract, raw_symbol, raw_exchange)

        return list(contracts.values())

    def _get_intraday_client(self) -> Any:
        if self.client is None:
            return None

        marketdata = getattr(self.client, "marketdata", None)
        if marketdata is None:
            self._prepare_realtime()
            marketdata = getattr(self.client, "marketdata", None)
            if marketdata is None:
                return None

        rest_client = getattr(marketdata, "rest_client", None)
        if rest_client is None:
            return None

        futopt_rest = getattr(rest_client, "futopt", None)
        if futopt_rest is None:
            return None

        return getattr(futopt_rest, "intraday", None)

    def _fetch_product_metadata(self, intraday: Any) -> Dict[str, Mapping[str, Any]]:
        product_metadata: Dict[str, Mapping[str, Any]] = {}
        product_params = [
            {"type": "FUTURE", "session": "REGULAR"},
            {"type": "FUTURE", "session": "AFTERHOURS"},
            {"type": "OPTION", "session": "REGULAR"},
            {"type": "OPTION", "session": "AFTERHOURS"},
        ]

        for params in product_params:
            try:
                response = self._call_rest_with_retry(
                    intraday.products,
                    exchange=self._default_exchange_code,
                    limit=2000,
                    **params,
                )
            except Exception as exc:  # pragma: no cover - vendor behaviour
                self.logger.debug("intraday.products failed for %s: %s", params, exc)
                continue

            for item in response.get("data") or []:
                symbol = str(item.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                existing = product_metadata.get(symbol, {})
                merged = {**existing, **item}
                product_metadata[symbol] = merged

        return product_metadata

    def _map_ticker_to_contract(
        self,
        ticker: Mapping[str, Any],
        product_metadata: Mapping[str, Mapping[str, Any]],
    ) -> Optional[Tuple[ContractData, str, str]]:
        raw_symbol = str(ticker.get("symbol") or "").strip()
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            return None

        product_descriptor = ticker.get("type") or ""
        product_key = self._match_product_symbol(symbol, product_metadata)
        metadata = product_metadata.get(product_key or "", {})
        product = normalize_product(product_descriptor, default=Product.FUTURES)

        name = ticker.get("name") or metadata.get("name") or symbol

        def _to_float(value: Any, default: float) -> float:
            if value is None:
                return default
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        size = _to_float(
            metadata.get("contractSize")
            or metadata.get("contractMultiplier")
            or metadata.get("multiplier")
            or 1,
            1.0,
        )
        pricetick = _to_float(metadata.get("tickSize") or ticker.get("tickSize") or 1, 1.0)

        exchange_code = (
            ticker.get("exchange")
            or metadata.get("exchange")
            or self._default_exchange_code
        )
        exchange = normalize_exchange(exchange_code, default=self._default_exchange_code)

        contract = ContractData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            name=str(name),
            product=product,
            size=size,
            pricetick=pricetick,
        )
        contract.min_volume = max(1.0, _to_float(metadata.get("minVolume") or metadata.get("minimumVolume"), 1.0))
        contract.history_data = True

        contract.extra = {
            "session": ticker.get("session"),
            "contractType": ticker.get("contractType") or metadata.get("contractType"),
            "flowGroup": ticker.get("flowGroup"),
            "rawSymbol": raw_symbol,
            "rawExchange": exchange_code,
            "canonicalSymbol": symbol,
            "canonicalExchange": getattr(exchange, "value", str(exchange)),
        }

        if product is Product.OPTION:
            self._populate_option_fields(contract, ticker, metadata)

        return contract, raw_symbol, str(exchange_code or "")

    def fetch_candles(
        self,
        symbol: str,
        *,
        session: Optional[str] = None,
        timeframe: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[BarData]:
        intraday = self._get_intraday_client()
        if intraday is None:
            raise RuntimeError("REST intraday client unavailable; call connect() first.")

        params: Dict[str, Any] = {"symbol": symbol}
        if session:
            params["session"] = session
        if timeframe:
            params["timeframe"] = timeframe
        if limit:
            params["limit"] = limit

        response = self._call_rest_with_retry(intraday.candles, **params)
        data_entries = response.get("data") or []
        exchange = response.get("exchange") or self._default_exchange_code

        bars: List[BarData] = []
        for entry in data_entries:
            entry_map = dict(entry)
            entry_map.setdefault("symbol", response.get("symbol") or symbol)
            entry_map.setdefault("exchange", exchange)
            entry_map.setdefault("timeframe", response.get("timeframe") or timeframe)
            entry_map.setdefault("date", entry.get("time"))
            bar = self._normalize_market_bar(entry_map)
            if bar:
                bars.append(bar)
        return bars

    def _resolve_history_timeframe(
        self,
        interval: Optional[Interval],
    ) -> Tuple[Optional[Any], Optional[int]]:
        value: Optional[Any] = getattr(interval, "value", interval) if interval is not None else None
        if value is None:
            return ("1m", 1)

        if isinstance(value, str):
            mapping: Dict[str, Tuple[Optional[Any], Optional[int]]] = {
                "1m": ("1m", 1),
                "3m": ("3m", 3),
                "5m": ("5m", 5),
                "15m": ("15m", 15),
                "30m": ("30m", 30),
                "60m": ("60m", 60),
                "1h": ("1h", 60),
                "d": ("1d", 1440),
                "1d": ("1d", 1440),
                "day": ("1d", 1440),
                "w": ("1w", 10080),
                "1w": ("1w", 10080),
                "week": ("1w", 10080),
                "tick": (None, None),
            }
            resolved = mapping.get(value.lower())
            if resolved:
                return resolved

        if isinstance(value, (int, float)):
            minutes = int(value)
            return (minutes, minutes)

        return (None, None)

    def _estimate_history_limit(
        self,
        start: Optional[datetime],
        end: Optional[datetime],
        minutes_per_bar: Optional[int],
    ) -> Optional[int]:
        if not minutes_per_bar or minutes_per_bar <= 0:
            return None

        total_minutes: Optional[float] = None
        if start and end:
            total_minutes = max((end - start).total_seconds() / 60, 0)
        elif start and not end:
            total_minutes = max((datetime.now(timezone.utc) - start).total_seconds() / 60, 0)

        if not total_minutes or total_minutes <= 0:
            return None

        estimated = int(total_minutes / minutes_per_bar) + 5
        return max(1, min(estimated, 2000))

    def _filter_history_window(
        self,
        bars: Sequence[BarData],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> List[BarData]:
        start_utc = self._ensure_utc(start)
        end_utc = self._ensure_utc(end)
        if not start_utc and not end_utc:
            return list(bars)

        filtered: List[BarData] = []
        for bar in bars:
            dt = getattr(bar, "datetime", None)
            if dt is None:
                continue
            compare_dt = self._ensure_utc(dt)
            if start_utc and compare_dt < start_utc:
                continue
            if end_utc and compare_dt > end_utc:
                continue
            filtered.append(bar)
        return filtered

    def _ensure_utc(self, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def fetch_trades_history(
        self,
        symbol: str,
        *,
        session: Optional[str] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[TradeData]:
        intraday = self._get_intraday_client()
        if intraday is None:
            raise RuntimeError("REST intraday client unavailable; call connect() first.")

        params: Dict[str, Any] = {"symbol": symbol}
        if session:
            params["session"] = session
        if offset is not None:
            params["offset"] = offset
        if limit is not None:
            params["limit"] = limit

        response = self._call_rest_with_retry(intraday.trades, **params)
        data_entries = response.get("data") or []
        exchange = response.get("exchange") or self._default_exchange_code

        trades: List[TradeData] = []
        for entry in data_entries:
            normalized_entry = {
                "matchTime": entry.get("time"),
                "price": entry.get("price"),
                "matchQty": entry.get("size"),
                "serial": entry.get("serial"),
                "tradeId": entry.get("serial"),
                "orderId": entry.get("orderId"),
                "side": entry.get("side"),
            }
            payload = {
                "channel": "trades",
                "symbol": symbol,
                "exchange": exchange,
                "trades": [normalized_entry],
            }
            trade = self._normalize_market_trade(payload)
            if trade:
                trades.append(trade)
        return trades

    def fetch_volume_profile(
        self,
        symbol: str,
        *,
        session: Optional[str] = None,
    ) -> List[Mapping[str, float]]:
        intraday = self._get_intraday_client()
        if intraday is None:
            raise RuntimeError("REST intraday client unavailable; call connect() first.")

        params: Dict[str, Any] = {"symbol": symbol}
        if session:
            params["session"] = session

        response = self._call_rest_with_retry(intraday.volumes, **params)
        data_entries = response.get("data") or []
        volumes: List[Mapping[str, float]] = []
        for entry in data_entries:
            volumes.append(
                {
                    "price": float(self._safe_float(entry.get("price"))),
                    "volume": float(self._safe_float(entry.get("volume"))),
                }
            )
        return volumes

    def _match_product_symbol(
        self, contract_symbol: str, product_metadata: Mapping[str, Mapping[str, Any]]
    ) -> Optional[str]:
        upper_symbol = contract_symbol.upper()
        best_match: Optional[str] = None
        best_length = 0
        for candidate in product_metadata.keys():
            if upper_symbol.startswith(candidate) and len(candidate) > best_length:
                best_length = len(candidate)
                best_match = candidate
        return best_match

    def _register_contract_aliases(
        self,
        contract: ContractData,
        raw_symbol: str,
        raw_exchange: str,
    ) -> None:
        vt_symbol = contract.vt_symbol
        canonical_symbol = normalize_symbol(contract.symbol)
        canonical_exchange = getattr(contract.exchange, "value", str(contract.exchange))

        normalized_raw_symbol = normalize_symbol(raw_symbol) or canonical_symbol
        raw_exchange_code = normalize_symbol(raw_exchange)

        # Base symbol aliases
        self._symbol_aliases.setdefault(canonical_symbol, vt_symbol)
        self._symbol_aliases.setdefault(normalized_raw_symbol, vt_symbol)
        self._symbol_aliases.setdefault(vt_symbol, vt_symbol)

        # Exchange-aware aliases
        self._symbol_exchange_aliases.setdefault(
            (normalized_raw_symbol, canonical_exchange),
            vt_symbol,
        )
        self._symbol_exchange_aliases.setdefault(
            (canonical_symbol, canonical_exchange),
            vt_symbol,
        )
        if raw_exchange_code:
            self._symbol_exchange_aliases.setdefault(
                (normalized_raw_symbol, raw_exchange_code),
                vt_symbol,
            )

    def resolve_vt_symbol(self, symbol: str, exchange: Optional[str] = None) -> Optional[str]:
        """
        Resolve a symbol (with optional exchange) into a canonical vt_symbol.
        """

        if not symbol:
            return None

        direct_candidate = symbol.upper()
        if direct_candidate in self.contracts:
            return direct_candidate

        normalized_symbol = normalize_symbol(symbol)
        if not normalized_symbol:
            return None

        if exchange:
            normalized_exchange = normalize_symbol(exchange)
            vt_symbol = self._symbol_exchange_aliases.get((normalized_symbol, normalized_exchange))
            if vt_symbol:
                return vt_symbol

            exchange_enum = normalize_exchange(exchange)
            canonical_exchange = getattr(exchange_enum, "value", str(exchange_enum))
            vt_symbol = self._symbol_exchange_aliases.get((normalized_symbol, canonical_exchange))
            if vt_symbol:
                return vt_symbol

        vt_symbol = self._symbol_aliases.get(normalized_symbol)
        if vt_symbol:
            return vt_symbol

        return None

    def find_contract(self, symbol: str, exchange: Optional[str] = None) -> Optional[ContractData]:
        """
        Retrieve a contract using raw symbol/exchange identifiers.
        """

        vt_symbol = self.resolve_vt_symbol(symbol, exchange)
        if not vt_symbol:
            return None
        return self.contracts.get(vt_symbol)

    def _populate_option_fields(
        self,
        contract: ContractData,
        ticker: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> None:
        strike = self._parse_option_strike(contract.symbol, metadata.get("symbol"))
        if strike is not None:
            contract.option_strike = strike

        underlying = metadata.get("underlyingSymbol")
        if underlying:
            exchange_value = getattr(contract.exchange, "value", str(contract.exchange))
            contract.option_underlying = f"{underlying}.{exchange_value}"

        expiry_date = ticker.get("settlementDate") or ticker.get("endDate")
        contract.option_expiry = self._parse_date(expiry_date)
        contract.option_listed = self._parse_date(ticker.get("startDate"))

        option_type = self._resolve_option_type(contract.symbol)
        if option_type:
            contract.option_type = option_type

    def _parse_option_strike(self, symbol: str, product_symbol: Any) -> Optional[float]:
        if not symbol:
            return None
        base = str(product_symbol or "").upper()
        prefix_len = len(base)
        if prefix_len and symbol.upper().startswith(base):
            suffix = symbol[prefix_len:]
        else:
            suffix = symbol
        numeric = "".join(ch for ch in suffix[:-2] if ch.isdigit())
        if not numeric:
            return None
        try:
            return float(numeric)
        except ValueError:
            return None

    def _resolve_option_type(self, symbol: str) -> Optional[OptionType]:
        if len(symbol) < 2:
            return None
        month_code = symbol[-2].upper()
        if month_code in "ABCDEFGHIJKL":
            return OptionType.CALL
        if month_code in "MNOPQRSTUVWX":
            return OptionType.PUT
        return None

    def _parse_date(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _prepare_realtime(self) -> None:
        if self.client is None:
            return
        token_method = getattr(self.client, "exchange_realtime_token", None)
        if callable(token_method):
            try:
                token_method()
            except Exception as exc:
                self.logger.debug("exchange_realtime_token() failed: %s", exc)

        init_method = getattr(self.client, "init_realtime", None)
        if callable(init_method):
            mode_candidates: list[Any] = []
            resolved_mode = self._resolve_ws_mode()
            if resolved_mode is not None:
                mode_candidates.append(resolved_mode)
            mode_candidates.append(None)
            initialised = False
            for candidate in mode_candidates:
                try:
                    if candidate is None:
                        init_method()
                    else:
                        init_method(candidate)
                    initialised = True
                    break
                except TypeError:
                    try:
                        if candidate is None:
                            init_method(mode=None)
                        else:
                            init_method(mode=candidate)
                        initialised = True
                        break
                    except Exception as exc:
                        self.logger.debug("init_realtime(mode=%s) failed: %s", candidate, exc)
                except Exception as exc:
                    self.logger.debug("init_realtime(%s) failed: %s", candidate, exc)
            if not initialised:
                self.logger.debug("Unable to initialise realtime market data; continuing without explicit mode.")

    def _ensure_websocket_client(self, *, register_handler: bool = False) -> Any:
        if self.client is None:
            raise RuntimeError("Gateway client unavailable.")

        with self._ws_lock:
            if self._ws_client is None:
                marketdata = getattr(self.client, "marketdata", None)
                if marketdata is None:
                    self._prepare_realtime()
                    marketdata = getattr(self.client, "marketdata", None)
                    if marketdata is None:
                        raise RuntimeError("marketdata client not exposed by SDK.")
                factory = getattr(marketdata, "websocket_client", None)
                if factory is None:
                    raise RuntimeError("websocket_client factory missing in marketdata.")
                futopt_client = getattr(factory, "futopt", None)
                if futopt_client is None:
                    raise RuntimeError("FutOpt websocket client unavailable in this SDK build.")
                self._ws_client = futopt_client

            if not self._ws_connected:
                try:
                    self._ws_client.connect()
                    self._ws_connected = True
                    self._ws_reconnect_attempts = 0
                    self._cancel_ws_reconnect()
                    self.logger.info(
                        "FutOpt websocket connected.",
                        extra={"gateway_state": "ws_connected"},
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Websocket connection failed: %s",
                        exc,
                        extra={"gateway_state": "ws_error"},
                    )
                    self._schedule_ws_reconnect()
                    raise
                self._resubscribe_all()
                self._start_token_refresh()
                self._start_ws_heartbeat()

            if register_handler:
                self._register_ws_handlers()

            return self._ws_client

    def _disconnect_websocket(self) -> None:
        with self._ws_lock:
            if self._ws_client is None:
                return

            self._unregister_ws_handlers()

            if self._ws_connected:
                disconnect = getattr(self._ws_client, "disconnect", None)
                if callable(disconnect):
                    try:
                        disconnect()
                    except Exception:
                        pass
                self._ws_connected = False

            self._ws_client = None
            self._active_subscriptions.clear()
            self._subscription_ids_by_key.clear()
            self._subscription_key_by_id.clear()
            self._cancel_ws_reconnect()
            self._stop_ws_heartbeat()

    def _resubscribe_all(self) -> None:
        if not self._ws_client or not self._active_subscriptions:
            return
        for channel, symbol, after_hours in list(self._active_subscriptions):
            payload = {"channel": channel, "symbol": symbol}
            if after_hours is not None:
                payload["afterHours"] = bool(after_hours)
            try:
                response = self._ws_client.subscribe(payload)
                parsed_ids = self._parse_subscription_ids(response, [symbol])
                if parsed_ids:
                    sub_id = parsed_ids.get(symbol)
                    if sub_id:
                        key = (channel, symbol, after_hours)
                        self._subscription_ids_by_key[key] = sub_id
                        self._subscription_key_by_id[sub_id] = key
                self.logger.debug(
                    "Re-subscribed websocket channel %s",
                    payload,
                    extra={
                        "channel": channel,
                        "symbol": symbol,
                        "gateway_state": "resubscribed",
                    },
                )
            except Exception as exc:
                self.logger.warning(
                    "Failed to re-subscribe %s: %s",
                    payload,
                    exc,
                    extra={
                        "channel": channel,
                        "symbol": symbol,
                        "gateway_state": "resubscribe_failed",
                    },
                )

    def _register_ws_handlers(self) -> None:
        if self._ws_handlers_registered or not self._ws_client:
            return
        registered = False
        self._ws_registered_events.clear()
        handler = getattr(self._ws_client, "on", None)
        if callable(handler):
            events: list[Tuple[str, Callable[..., None]]] = [
                ("message", self._handle_ws_message),
                ("disconnect", self._handle_ws_disconnect),
                ("error", self._handle_ws_error),
                ("authenticated", self._handle_ws_authenticated),
            ]
            for event_name, callback in events:
                try:
                    handler(event_name, callback)
                    self._ws_registered_events.append((event_name, callback))
                except Exception as exc:
                    self.logger.debug("Registering websocket handler %s failed: %s", event_name, exc)
            if self._ws_registered_events:
                registered = True

        self._register_sdk_callbacks()
        if self._ws_sdk_callbacks:
            registered = True

        self._ws_handlers_registered = registered

    def _register_sdk_callbacks(self) -> None:
        self._ws_sdk_callbacks.clear()
        if not self._ws_client:
            return

        setter_pairs: tuple[tuple[str, Callable[..., None]], ...] = (
            ("set_on_event", self._handle_ws_sdk_event),
            ("set_on_error", self._handle_ws_sdk_error),
        )
        for method_name, handler in setter_pairs:
            method = getattr(self._ws_client, method_name, None)
            if not callable(method):
                continue
            try:
                method(handler)
                self._ws_sdk_callbacks.append((method_name, handler))
                self.logger.debug("Registered websocket callback via %s", method_name)
            except Exception as exc:
                self.logger.debug("Registering websocket callback %s failed: %s", method_name, exc)

    def _unregister_sdk_callbacks(self) -> None:
        if not self._ws_client or not self._ws_sdk_callbacks:
            return
        for method_name, _handler in self._ws_sdk_callbacks:
            setter = getattr(self._ws_client, method_name, None)
            if callable(setter):
                try:
                    setter(None)
                except Exception:
                    pass
        self._ws_sdk_callbacks.clear()

    def _unregister_ws_handlers(self) -> None:
        if not self._ws_client or not self._ws_handlers_registered:
            return
        off = getattr(self._ws_client, "off", None)
        if callable(off):
            for event_name, callback in self._ws_registered_events:
                try:
                    off(event_name, callback)
                except Exception:
                    pass
        self._ws_registered_events.clear()
        self._unregister_sdk_callbacks()
        self._ws_handlers_registered = False
        self._cancel_token_refresh()

    def _start_token_refresh(self) -> None:
        if self._closing or self.client is None:
            return
        interval_env = os.getenv("FUBON_TOKEN_REFRESH_INTERVAL")
        if interval_env:
            try:
                interval = max(60, int(interval_env))
            except ValueError:
                self.logger.debug("Invalid FUBON_TOKEN_REFRESH_INTERVAL=%s", interval_env)
                interval = self._token_refresh_interval
        else:
            interval = self._token_refresh_interval
        timer = Timer(interval, self._refresh_token)
        timer.daemon = True
        self._token_timer = timer
        timer.start()

    def _cancel_token_refresh(self) -> None:
        if self._token_timer:
            self._token_timer.cancel()
            self._token_timer = None

    def _start_ws_heartbeat(self) -> None:
        if self._closing or self._ws_ping_interval <= 0:
            return
        with self._ws_lock:
            if not self._ws_connected or not self._ws_client:
                return
        self._stop_ws_heartbeat()
        timer = Timer(self._ws_ping_interval, self._send_ws_ping)
        timer.daemon = True
        self._ws_ping_timer = timer
        timer.start()

    def _stop_ws_heartbeat(self) -> None:
        if self._ws_ping_timer:
            self._ws_ping_timer.cancel()
            self._ws_ping_timer = None

    def _send_ws_ping(self) -> None:
        with self._ws_lock:
            self._ws_ping_timer = None
            ws = self._ws_client
            connected = self._ws_connected
        if not ws or not connected or self._closing:
            return

        ping_method = getattr(ws, "ping", None) or getattr(ws, "send_ping", None)
        try:
            if callable(ping_method):
                ping_method()
            else:
                send_method = getattr(ws, "send", None) or getattr(ws, "write_message", None)
                if callable(send_method):
                    try:
                        send_method(json.dumps({"event": "ping"}))
                    except TypeError:
                        send_method({"event": "ping"})
        except Exception as exc:
            self.logger.debug(
                "Websocket ping failed: %s",
                exc,
                extra={"gateway_state": "ws_ping_failed"},
            )
        finally:
            self._start_ws_heartbeat()

    def _refresh_token(self) -> None:
        with self._ws_lock:
            self._token_timer = None
        if self._closing:
            return
        method = getattr(self.client, "exchange_realtime_token", None)
        if callable(method):
            try:
                method()
                self.logger.debug("exchange_realtime_token() executed for heartbeat.")
            except Exception as exc:  # pragma: no cover - vendor behaviour
                message = f"exchange_realtime_token() heartbeat failed: {exc}"
                self.logger.warning(
                    message,
                    extra={"gateway_state": "heartbeat_failed"},
                )
                self._put_event(EVENT_LOG, message)
                self._prepare_realtime()
                with self._ws_lock:
                    self._ws_connected = False
                if self._should_reconnect_after_error(exc):
                    self._schedule_ws_reconnect()
        else:
            self.logger.debug("exchange_realtime_token() not available on client.")
        self._start_token_refresh()

    def _populate_account_metadata(self, setting: Optional[Mapping[str, Any]] = None) -> None:
        data = getattr(self.login_response, "data", None)
        if isinstance(data, (list, tuple)):
            self.accounts = list(data)
        else:
            self.accounts = []

        self.account_map = {}
        self.account_metadata = {}
        for account in self.accounts:
            metadata = self._extract_account_metadata(account)
            account_id = metadata.get("account_id")
            if not account_id:
                continue
            self.account_map[account_id] = account
            self.account_metadata[account_id] = metadata

        self.primary_account = None
        self.primary_account_id = None

        preferred_account = None
        if setting:
            preferred_account = (
                setting.get("account_id")
                or setting.get("account")
                or setting.get("primary_account")
            )
        if not preferred_account:
            preferred_account = os.getenv("FUBON_PRIMARY_ACCOUNT")

        if preferred_account:
            preferred_account = str(preferred_account).strip()
            account = self.account_map.get(preferred_account)
            if account:
                self.primary_account = account
                self.primary_account_id = preferred_account
                self.logger.debug("Primary account set via configuration: %s", preferred_account)
            else:
                self.logger.warning(
                    "Requested account %s not found; falling back to default.",
                    preferred_account,
                    extra={"gateway_state": "account_warning"},
                )

        if not self.primary_account and self.accounts:
            for account in self.accounts:
                account_id = getattr(account, "account", None)
                if account_id is not None:
                    self.primary_account = account
                    self.primary_account_id = str(account_id)
                    break
            if not self.primary_account:
                self.primary_account = self.accounts[0]
                account_id = getattr(self.primary_account, "account", None)
                self.primary_account_id = str(account_id) if account_id is not None else None

        if self.primary_account_id:
            self.logger.debug("Primary account detected: %s", self.primary_account_id)
        if self.account_map:
            self.logger.debug("Available accounts: %s", ", ".join(sorted(self.account_map.keys())))

        if self.order_api:
            self.order_api.set_account_lookup(self.account_map)

    def _extract_account_metadata(self, account: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        account_id = self._get_metadata_value(account, ("account", "account_id", "acct", "id"))
        if account_id is not None:
            metadata["account_id"] = str(account_id).strip()
        account_name = self._get_metadata_value(account, ("account_name", "name", "accountName"))
        if account_name:
            metadata["account_name"] = str(account_name).strip()
        account_type = self._get_metadata_value(account, ("account_type", "type", "acctType"))
        if account_type:
            metadata["account_type"] = str(account_type).strip()
        broker_id = self._get_metadata_value(account, ("broker_id", "broker", "brokerId"))
        if broker_id:
            metadata["broker_id"] = str(broker_id).strip()
        market = self._get_metadata_value(account, ("market", "exchange"))
        if market:
            metadata["market"] = str(market).strip()
        default_flag = self._get_metadata_value(account, ("default", "is_default", "primary"))
        if default_flag is not None:
            metadata["is_default"] = bool(default_flag)
        return metadata

    def _get_metadata_value(self, source: Any, candidates: Sequence[str]) -> Any:
        for key in candidates:
            if isinstance(source, Mapping) and key in source:
                value = source[key]
            else:
                try:
                    value = getattr(source, key, None)
                except Exception:  # pragma: no cover - vendor specific attribute access
                    value = None
            if value not in (None, ""):
                return value
        return None

    def _resolve_candidate_account_id(self, payload: Any) -> Optional[str]:
        if isinstance(payload, Mapping):
            value = self._get_metadata_value(payload, ("account", "account_id", "acct", "id"))
            if value is not None:
                return str(value).strip()
        elif hasattr(payload, "__dict__"):
            value = self._get_metadata_value(payload, ("account", "account_id", "acct", "id"))
            if value is not None:
                return str(value).strip()
        if isinstance(payload, str):
            stripped = payload.strip()
            return stripped or None
        return None

    def _register_order_callbacks(self) -> None:
        if self.client is None:
            return

        callback_pairs = (
            ("set_on_futopt_order", self._handle_order_event),
            ("set_on_order_changed", self._handle_order_event),
            ("set_on_futopt_filled", self._handle_trade_event),
            ("set_on_filled", self._handle_trade_event),
        )
        for method_name, handler in callback_pairs:
            method = getattr(self.client, method_name, None)
            if not callable(method):
                continue
            try:
                method(handler)
                self.logger.debug("Registered callback via %s", method_name)
            except Exception as exc:
                self.logger.debug("Registering %s failed: %s", method_name, exc)

    def _handle_ws_message(self, message: str) -> None:
        if not self.market_api:
            return
        events = self.market_api.parse_market_events(message)
        for event in events:
            payload = dict(event.payload)
            if event.channel and "channel" not in payload:
                payload["channel"] = event.channel
            payload.setdefault("event_type", event.event_type)

            channel = (event.channel or "").lower()
            if event.event_type == "orderbook" and event.tick:
                event.tick.gateway_name = self.gateway_name
                payload["tick"] = event.tick
                self._put_event(EVENT_TICK, event.tick)
            else:
                if channel in {"trades", "trade"} or event.event_type == "trade":
                    trade = self._normalize_market_trade(event.payload)
                    if trade:
                        payload["trade"] = trade
                elif channel in {"candles", "candle", "aggregates", "aggregate"}:
                    bar = self._normalize_market_bar(event.payload)
                    if bar:
                        payload["bar"] = bar

            self._put_event(EVENT_FUBON_MARKET_RAW, payload)

    def _handle_ws_disconnect(self, *args: Any, **kwargs: Any) -> None:
        if self._closing:
            return
        message = f"FutOpt websocket disconnected: args={args} kwargs={kwargs}"
        self.logger.warning(
            message,
            extra={"gateway_state": "ws_disconnected"},
        )
        self._put_event(EVENT_LOG, message)
        with self._ws_lock:
            self._ws_connected = False
        self._schedule_ws_reconnect()

    def _handle_ws_error(self, error: Any) -> None:
        if self._closing:
            return
        message = f"FutOpt websocket error: {error}"
        self.logger.warning(
            message,
            extra={"gateway_state": "ws_error"},
        )
        self._put_event(EVENT_LOG, message)
        with self._ws_lock:
            self._ws_connected = False
        if self._should_reconnect_after_error(error):
            self._schedule_ws_reconnect()

    def _handle_ws_authenticated(self, *_args: Any, **_kwargs: Any) -> None:
        self.logger.debug(
            "FutOpt websocket authenticated.",
            extra={"gateway_state": "ws_authenticated"},
        )
        with self._ws_lock:
            self._ws_connected = True
        self._cancel_ws_reconnect()
        self._start_token_refresh()

    def _handle_ws_sdk_event(self, event: Any) -> None:
        message = f"FutOpt websocket event: {event}"
        self.logger.info(
            message,
            extra={"gateway_state": "ws_event"},
        )
        self._put_event(EVENT_LOG, message)
        if isinstance(event, Mapping):
            status = str(
                event.get("status") or event.get("event") or event.get("code") or ""
            ).lower()
            if status in {"unauthorized", "unauthenticated", "token_expired"}:
                with self._ws_lock:
                    self._ws_connected = False
                self._schedule_ws_reconnect()

    def _handle_ws_sdk_error(self, error: Any) -> None:
        message = f"FutOpt websocket SDK error: {error}"
        self.logger.error(message, extra={"gateway_state": "ws_error"})
        self._put_event(EVENT_LOG, message)
        with self._ws_lock:
            self._ws_connected = False
        if self._should_reconnect_after_error(error):
            self._schedule_ws_reconnect()

    def _handle_order_event(self, payload: Any) -> None:
        if not self.order_api or not isinstance(payload, Mapping):
            return
        try:
            order = self.order_api.to_order_data(payload)
            self._put_event(EVENT_ORDER, order)
        except Exception as exc:  # pragma: no cover - vendor payload
            self.logger.debug("Failed to map order payload %s: %s", payload, exc)

    def _handle_trade_event(self, payload: Any) -> None:
        if not self.order_api or not isinstance(payload, Mapping):
            return
        try:
            trade = self.order_api.to_trade_data(payload)
            self._put_event(EVENT_TRADE, trade)
        except Exception as exc:  # pragma: no cover - vendor payload
            self.logger.debug("Failed to map trade payload %s: %s", payload, exc)

    def _should_reconnect_after_error(self, error: Any) -> bool:
        message = str(getattr(error, "message", error)).lower()
        non_retriable_tokens = (
            "invalid channel",
            "invalid symbol",
            "unknown channel",
            "unknown symbol",
            "bad request",
            "parameter",
        )
        return not any(token in message for token in non_retriable_tokens)

    def _schedule_ws_reconnect(self) -> None:
        if self._closing:
            return
        with self._ws_lock:
            if self._ws_reconnect_timer and self._ws_reconnect_timer.is_alive():
                return
            self._ws_reconnect_attempts = min(self._ws_reconnect_attempts + 1, 8)
            delay = min(60.0, 2.0 ** self._ws_reconnect_attempts)
            self.logger.info(
                "Scheduling websocket reconnect in %.1f seconds (attempt %d)",
                delay,
                self._ws_reconnect_attempts,
                extra={
                    "gateway_state": "ws_reconnect_scheduled",
                    "latency_ms": int(delay * 1000),
                },
            )
            timer = Timer(delay, self._perform_ws_reconnect)
            timer.daemon = True
            self._ws_reconnect_timer = timer
            timer.start()

    def _cancel_ws_reconnect(self) -> None:
        with self._ws_lock:
            if self._ws_reconnect_timer:
                self._ws_reconnect_timer.cancel()
                self._ws_reconnect_timer = None
            self._ws_reconnect_attempts = 0

    def _perform_ws_reconnect(self) -> None:
        with self._ws_lock:
            self._ws_reconnect_timer = None
        if self._closing:
            return
        try:
            self._prepare_realtime()
            self._ensure_websocket_client(register_handler=True)
            self.logger.info(
                "Websocket reconnect successful.",
                extra={"gateway_state": "ws_reconnected"},
            )
            self._start_token_refresh()
        except Exception as exc:  # pragma: no cover - vendor behaviour
            self.logger.warning(
                "Websocket reconnect failed: %s",
                exc,
                extra={"gateway_state": "ws_reconnect_failed"},
            )
            self._schedule_ws_reconnect()

    def _apply_account_context(self, account_id: str) -> bool:
        if self.client is None:
            return True
        setter_candidates = (
            "switch_account",
            "set_account",
            "set_current_account",
            "select_account",
        )
        for name in setter_candidates:
            method = getattr(self.client, name, None)
            if not callable(method):
                continue
            try:
                result = method(account_id)
                success = True if result is None else bool(result)
                if success:
                    self.logger.debug("Applied account context via %s", name)
                    return True
                self.logger.warning(
                    "Account setter %s returned falsy result while switching to %s.",
                    name,
                    account_id,
                )
            except Exception as exc:  # pragma: no cover - vendor behaviour
                self.logger.warning(
                    "Applying account via %s failed: %s",
                    name,
                    exc,
                    extra={"gateway_state": "account_warning"},
                )
        for attr_name in ("account_id", "account", "current_account"):
            if hasattr(self.client, attr_name):
                try:
                    setattr(self.client, attr_name, account_id)
                    self.logger.debug("Set client.%s = %s", attr_name, account_id)
                    return True
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "Setting client.%s failed: %s",
                        attr_name,
                        exc,
                        extra={"gateway_state": "account_warning"},
                    )
        return False

    def _validate_account_context(self, account_id: str) -> bool:
        if self.client is None:
            return True
        getter_candidates = (
            "get_current_account",
            "current_account",
            "account_id",
            "account",
        )
        for name in getter_candidates:
            attr = getattr(self.client, name, None)
            if attr is None:
                continue
            try:
                current = attr() if callable(attr) else attr
            except Exception:  # pragma: no cover - vendor behaviour
                continue
            candidate = self._resolve_candidate_account_id(current)
            if not candidate:
                continue
            if candidate != account_id:
                warning = (
                    f"SDK reports active account {candidate} after switching to {account_id}."
                )
                self.logger.warning(warning, extra={"gateway_state": "account_warning"})
                self._put_event(EVENT_LOG, warning)
                return False
            return True
        return True

    def switch_account(self, account_id: str) -> bool:
        account_id = str(account_id).strip()
        account = self.account_map.get(account_id)
        if not account:
            self.logger.warning(
                "Account %s not available in login response.",
                account_id,
                extra={"gateway_state": "account_warning"},
            )
            return False

        previous_primary = self.primary_account
        previous_primary_id = self.primary_account_id
        previous_order_account: Optional[str] = None
        if self.order_api:
            previous_order_account = self.order_api.account_id

        applied = self._apply_account_context(account_id)
        if not applied:
            self.logger.warning(
                "Account switch to %s failed: SDK refused to change context.", account_id
            )
            if previous_primary_id and previous_primary_id != account_id:
                self._apply_account_context(previous_primary_id)
            return False

        success = self._validate_account_context(account_id)
        if success:
            self.primary_account = account
            self.primary_account_id = account_id
            if self.order_api:
                self.order_api.account_id = account_id
            self.write_log(f"Switched primary account to {account_id}.")
            return True

        if self.order_api:
            self.order_api.account_id = previous_order_account
        self.primary_account = previous_primary
        self.primary_account_id = previous_primary_id
        if previous_primary_id and previous_primary_id != account_id:
            self._apply_account_context(previous_primary_id)
        self.logger.warning(
            "Account switch to %s failed validation; continuing to use %s.",
            account_id,
            previous_primary_id or "current session",
            extra={"gateway_state": "account_warning"},
        )
        return False

    def get_available_accounts(self) -> Sequence[str]:
        return list(sorted(self.account_metadata.keys()))

    def get_account_metadata(self) -> Sequence[Mapping[str, Any]]:
        return [dict(metadata) for metadata in self.account_metadata.values()]

    def _put_event(self, event_type: str, data: Any) -> None:
        payload = data
        if event_type == EVENT_LOG and isinstance(data, str):
            try:
                payload = LogData(msg=data, gateway_name=self.gateway_name)
            except Exception:
                payload = data
        event = Event(event_type, payload)
        put = getattr(self.event_engine, "put", None)
        if callable(put):
            put(event)
        else:
            self.event_engine(event)

    def write_log(self, message: str, *, state: str = "info") -> None:
        super().write_log(message)
        self.logger.info(message, extra={"gateway_state": state})

    # ------------------------------------------------------------------
    # Convenience aliases (legacy compatibility)

    def stop(self) -> None:  # pragma: no cover - legacy entry point
        self.close()
