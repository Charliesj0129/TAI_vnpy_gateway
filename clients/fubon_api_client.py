"""
Wrappers around the official `fubon_neo` SDK so that downstream tooling can
reuse a unified interface for market data subscriptions.

The previous implementation attempted to hand-roll REST authentication and
websocket management which diverged from the behaviour described in the vendor
documentation.  The SDK already exposes helpers to authenticate, initialise
market data, issue REST calls, and manage websocket subscriptions – this module
now delegates to those utilities and only focuses on bridging them with
asyncio-friendly callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Tuple

from fubon_neo.sdk import FubonSDK, Mode
from fubon_neo.fugle_marketdata.constants import (
    AUTHENTICATED_EVENT,
    CONNECT_EVENT,
    DISCONNECT_EVENT,
    ERROR_EVENT,
    MESSAGE_EVENT,
)

LOGGER = logging.getLogger("vnpy_fubon.clients.api")


class ClientState(Enum):
    """Lifecycle phases of the streaming client."""

    IDLE = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    STOPPED = auto()


@dataclass
class StreamingCredentials:
    """
    Certificate-based credentials required by the Fubon SDK.

    Values default to the standard environment variables when omitted so that
    scripts can rely on `.env` or user-provided shell configuration.
    """

    user_id: Optional[str] = None
    user_password: Optional[str] = None
    ca_path: Optional[str] = None
    ca_password: Optional[str] = None

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned.startswith(("\"", "'")) and cleaned.endswith(("\"", "'")) and len(cleaned) >= 2:
            cleaned = cleaned[1:-1]
        return cleaned or None

    def __post_init__(self) -> None:
        env = os.environ
        if self.user_id is None:
            self.user_id = env.get("FUBON_USER_ID")
        if self.user_password is None:
            self.user_password = env.get("FUBON_USER_PASSWORD")
        if self.ca_path is None:
            self.ca_path = env.get("FUBON_CA_PATH")
        if self.ca_password is None:
            self.ca_password = env.get("FUBON_CA_PASSWORD")

        self.user_id = self._clean(self.user_id)
        self.user_password = self._clean(self.user_password)
        self.ca_path = self._clean(self.ca_path)
        self.ca_password = self._clean(self.ca_password)

        if self.ca_path and not os.path.exists(self.ca_path):
            LOGGER.warning("FUBON_CA_PATH does not exist: %s", self.ca_path)

    def is_complete(self) -> bool:
        return all([self.user_id, self.user_password, self.ca_path, self.ca_password])


@dataclass
class Subscription:
    """
    Parameters describing a websocket subscription.

    The Fubon SDK expects one channel per subscription call; the helper will
    expand multi-channel requests automatically.
    """

    symbol: str
    channels: Sequence[str]
    depth: Optional[int] = None
    after_hours: Optional[bool] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class _FubonSDKSession:
    """
    Thin lifecycle helper that keeps a logged-in `FubonSDK` instance alive.
    """

    def __init__(
        self,
        credentials: StreamingCredentials,
        *,
        mode: Mode,
        sdk_factory: Callable[[], FubonSDK] = FubonSDK,
    ) -> None:
        self._credentials = credentials
        self._mode = mode
        self._sdk_factory = sdk_factory
        self._sdk: Optional[FubonSDK] = None
        self.login_response: Any = None

    def start(self) -> FubonSDK:
        if self._sdk is not None:
            return self._sdk

        if not self._credentials.is_complete():
            raise RuntimeError("Missing Fubon credentials; check environment variables or .env settings.")

        sdk = self._sdk_factory()
        LOGGER.info("Logging into Fubon SDK for user %s", self._credentials.user_id)
        self.login_response = sdk.login(
            self._credentials.user_id,
            self._credentials.user_password,
            self._credentials.ca_path,
            self._credentials.ca_password,
        )
        LOGGER.info("Initialising realtime market data channel using mode=%s", self._mode.name)
        sdk.init_realtime(self._mode)
        self._sdk = sdk
        return sdk

    def stop(self) -> None:
        if self._sdk is None:
            return
        try:
            if hasattr(self._sdk, "logout"):
                self._sdk.logout()
        except Exception as exc:  # pragma: no cover - SDK specific behaviour
            LOGGER.warning("Fubon SDK logout raised an exception: %s", exc)
        finally:
            self._sdk = None

    @property
    def websocket_client(self):
        sdk = self._sdk
        if sdk is None or not hasattr(sdk, "marketdata"):
            raise RuntimeError("Realtime market data has not been initialised; call start() first.")
        return sdk.marketdata.websocket_client.futopt

    @property
    def rest_client(self):
        sdk = self._sdk
        if sdk is None or not hasattr(sdk, "marketdata"):
            raise RuntimeError("Realtime market data has not been initialised; call start() first.")
        return sdk.marketdata.rest_client.futopt


class FubonAPIClient:
    """
    Async-friendly wrapper over the official Fubon websocket client.
    """

    def __init__(
        self,
        *,
        credentials: Optional[StreamingCredentials] = None,
        mode: Mode | str = Mode.Normal,
        on_message: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        on_state_change: Optional[Callable[[ClientState], None]] = None,
        sdk_factory: Callable[[], FubonSDK] = FubonSDK,
        auto_reconnect: bool = True,
    ) -> None:
        self.credentials = credentials or StreamingCredentials()
        if isinstance(mode, str):
            normalized = mode.strip().lower()
            resolved_mode = None
            for item in Mode:
                if item.name.lower() == normalized:
                    resolved_mode = item
                    break
            if resolved_mode is None:
                raise ValueError(
                    f"Unknown realtime mode '{mode}'. Expected one of {[item.name for item in Mode]}"
                )
        else:
            resolved_mode = mode

        self._session = _FubonSDKSession(self.credentials, mode=resolved_mode, sdk_factory=sdk_factory)
        self._on_message = on_message
        self._on_state_change = on_state_change

        self._state = ClientState.IDLE
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._subscriptions: Dict[str, Subscription] = {}
        self._subscription_ids: Dict[Tuple[str, str], str] = {}
        self._pending_tasks: set[asyncio.Future[Any]] = set()
        self._ws_handlers_registered = False
        self._auto_reconnect_default = auto_reconnect
        self._auto_reconnect_enabled = auto_reconnect
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_future: Optional[Future[Any]] = None
        self._stopping = False

    # ------------------------------------------------------------------ #
    # Lifecycle helpers

    @property
    def state(self) -> ClientState:
        return self._state

    def _set_state(self, state: ClientState) -> None:
        if self._state is state:
            return
        self._state = state
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:  # pragma: no cover - callback errors should not blow up
                LOGGER.exception("State change callback raised an exception.")

    async def start(self) -> None:
        if self._state is ClientState.CONNECTED:
            return

        self._set_state(ClientState.CONNECTING)
        self._stopping = False
        self._auto_reconnect_enabled = self._auto_reconnect_default
        self._loop = asyncio.get_running_loop()
        sdk = await asyncio.to_thread(self._session.start)
        self._ws = sdk.marketdata.websocket_client.futopt
        self._register_ws_handlers()
        await asyncio.to_thread(self._ws.connect)
        self._set_state(ClientState.CONNECTED)
        if self._subscriptions:
            await self._restore_subscriptions()

    async def stop(self) -> None:
        if self._state is ClientState.STOPPED:
            return

        self._set_state(ClientState.STOPPED)
        self._stopping = True
        self._auto_reconnect_enabled = False
        if self._reconnect_future and not self._reconnect_future.done():
            self._reconnect_future.cancel()
        self._reconnect_future = None
        if self._ws is not None:
            await asyncio.to_thread(self._ws.disconnect)
            self._deregister_ws_handlers()
        self._ws = None
        await asyncio.to_thread(self._session.stop)

        # Ensure pending callbacks are cleaned up
        while self._pending_tasks:
            future = self._pending_tasks.pop()
            future.cancel()

    # ------------------------------------------------------------------ #
    # Subscription helpers

    async def subscribe(self, subscription: Subscription) -> None:
        if self._ws is None:
            raise RuntimeError("Websocket client not started. Call start() first.")

        self._subscriptions[subscription.symbol] = subscription
        for channel in subscription.channels:
            channel_name = channel.lower()
            if channel_name == "orderbook":
                channel_name = "books"
            payload: Dict[str, Any] = {"channel": channel_name}
            symbols = subscription.extra.get("symbols") if isinstance(subscription.extra, Mapping) else None
            if symbols and isinstance(symbols, Sequence):
                payload["symbols"] = list(symbols)
            else:
                payload["symbols"] = [subscription.symbol]
            if subscription.depth is not None and channel_name in {"books", "orderbook"}:
                payload["depth"] = subscription.depth
            if subscription.after_hours is not None:
                payload["afterHours"] = bool(subscription.after_hours)
            extra_items = (
                {key: value for key, value in subscription.extra.items() if key not in {"symbols", "symbol"}}
                if isinstance(subscription.extra, Mapping)
                else {}
            )
            payload.update(extra_items)
            response = await asyncio.to_thread(self._ws.subscribe, payload)
            parsed_ids = self._parse_subscription_ids(response, payload.get("symbols", []))
            sub_id = parsed_ids.get(subscription.symbol)
            if sub_id:
                self._subscription_ids[(channel_name, subscription.symbol)] = sub_id

    async def unsubscribe(self, symbol: str) -> None:
        if self._ws is None:
            return
        sub = self._subscriptions.pop(symbol, None)
        if not sub:
            return
        ids: list[str] = []
        fallback_payloads: list[Dict[str, Any]] = []
        for channel in sub.channels:
            channel_name = channel.lower()
            if channel_name == "orderbook":
                channel_name = "books"
            key = (channel_name, symbol)
            sub_id = self._subscription_ids.pop(key, None)
            if sub_id:
                ids.append(sub_id)
            else:
                fallback_payloads.append({"channel": channel_name, "symbol": symbol})
        if ids:
            payload = {"ids": ids}
            await asyncio.to_thread(self._ws.unsubscribe, payload)
        for payload in fallback_payloads:
            await asyncio.to_thread(self._ws.unsubscribe, payload)

    def _parse_subscription_ids(self, response: Any, symbols: Sequence[str]) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        targets = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]

        def record(symbol: Any, value: Any) -> None:
            if symbol is None or value in (None, "", "null"):
                return
            symbol_str = str(symbol).strip()
            if symbol_str in targets:
                parsed[symbol_str] = str(value).strip()

        def walk(payload: Any) -> None:
            if payload is None:
                return
            if isinstance(payload, Mapping):
                ids_value = payload.get("ids")
                if isinstance(ids_value, (list, tuple)):
                    for symbol, sub_id in zip(symbols, ids_value):
                        record(symbol, sub_id)
                id_value = (
                    payload.get("id")
                    or payload.get("subscriptionId")
                    or payload.get("subscription_id")
                    or payload.get("channelId")
                    or payload.get("channel_id")
                )
                symbol_value = payload.get("symbol") or payload.get("code") or payload.get("target")
                if symbol_value is not None:
                    record(symbol_value, id_value)
                elif id_value is not None and len(targets) == 1:
                    record(targets[0], id_value)
                for key in ("data", "result", "results", "responses", "subscriptions"):
                    if key in payload:
                        walk(payload[key])
                attr_data = getattr(payload, "data", None)
                if attr_data is not None and attr_data is not payload:
                    walk(attr_data)
                return
            attr_data = getattr(payload, "data", None)
            if attr_data is not None and attr_data is not payload:
                walk(attr_data)
            if isinstance(payload, (list, tuple, set)):
                for item in payload:
                    walk(item)

        walk(response)
        return parsed

    # ------------------------------------------------------------------ #
    # Websocket event wiring

    def _register_ws_handlers(self) -> None:
        if self._ws is None or self._ws_handlers_registered:
            return
        self._ws.on(MESSAGE_EVENT, self._handle_ws_message)
        self._ws.on(ERROR_EVENT, self._handle_ws_error)
        self._ws.on(DISCONNECT_EVENT, self._handle_ws_disconnect)
        self._ws.on(CONNECT_EVENT, self._handle_ws_connect)
        self._ws.on(AUTHENTICATED_EVENT, self._handle_ws_authenticated)
        self._ws_handlers_registered = True

    def _deregister_ws_handlers(self) -> None:
        if self._ws is None or not self._ws_handlers_registered:
            return
        try:
            self._ws.off(MESSAGE_EVENT, self._handle_ws_message)
            self._ws.off(ERROR_EVENT, self._handle_ws_error)
            self._ws.off(DISCONNECT_EVENT, self._handle_ws_disconnect)
            self._ws.off(CONNECT_EVENT, self._handle_ws_connect)
            self._ws.off(AUTHENTICATED_EVENT, self._handle_ws_authenticated)
        except Exception:  # pragma: no cover - best effort
            LOGGER.debug("Failed to deregister websocket handlers.", exc_info=True)
        finally:
            self._ws_handlers_registered = False

    # Event emitters ---------------------------------------------------- #

    def _handle_ws_connect(self, *_: Any) -> None:
        LOGGER.info("Fubon market websocket connected.")

    def _handle_ws_authenticated(self, *_: Any) -> None:
        LOGGER.info("Fubon market websocket authenticated.")

    def _handle_ws_disconnect(self, *_: Any) -> None:
        LOGGER.warning("Fubon market websocket disconnected.")
        self._set_state(ClientState.STOPPED)
        self._schedule_reconnect()

    def _handle_ws_error(self, error: Any) -> None:
        LOGGER.error("Fubon market websocket reported error: %s", error)

    def _handle_ws_message(self, raw: Any) -> None:
        if self._on_message is None:
            return

        try:
            payload = json.loads(raw)
        except Exception:
            LOGGER.debug("Failed to decode websocket payload: %s", raw, exc_info=True)
            payload = {"event": "raw", "data": raw}

        try:
            result = self._on_message(payload)
        except Exception:  # pragma: no cover - synchronous callback error
            LOGGER.exception("on_message callback raised an exception.")
            return

        if asyncio.iscoroutine(result):
            if self._loop is None:
                LOGGER.warning("Event loop unavailable; dropping websocket payload.")
                return
            future = asyncio.run_coroutine_threadsafe(result, self._loop)
            self._pending_tasks.add(future)
            future.add_done_callback(self._pending_tasks.discard)

    # ------------------------------------------------------------------ #
    # REST convenience accessors

    @property
    def rest_client(self):
        """
        Expose the FutOpt REST client after `start()` has completed.
        """

        return self._session.rest_client

    # ------------------------------------------------------------------ #
    # Internal helpers

    async def _restore_subscriptions(self) -> None:
        for subscription in list(self._subscriptions.values()):
            await self.subscribe(subscription)

    def _schedule_reconnect(self) -> None:
        if not self._auto_reconnect_enabled or self._stopping or self._loop is None:
            return
        if self._reconnect_future and not self._reconnect_future.done():
            return
        future = asyncio.run_coroutine_threadsafe(self._auto_reconnect_loop(), self._loop)
        self._reconnect_future = future

    async def _auto_reconnect_loop(self) -> None:
        async with self._reconnect_lock:
            if self._stopping or not self._auto_reconnect_enabled:
                return
            delay = 1.0
            while self._auto_reconnect_enabled and not self._stopping:
                try:
                    await asyncio.to_thread(self._session.stop)
                except Exception:
                    LOGGER.debug("Error stopping SDK session during reconnect.", exc_info=True)
                self._ws = None
                self._ws_handlers_registered = False
                try:
                    await self.start()
                    LOGGER.info("Fubon market websocket reconnected successfully.")
                    return
                except Exception as exc:  # pragma: no cover - vendor behaviour
                    LOGGER.exception("Auto reconnect attempt failed; retrying. error=%s", exc)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)


# Backwards-compatible aliases for legacy imports --------------------------------

FubonCredentials = StreamingCredentials
StreamingDataClient = FubonAPIClient
WebSocketTransport = None  # legacy compatibility; no longer used
TokenBundle = None  # legacy compatibility; no longer used
FubonRESTClient = None  # legacy compatibility; no longer used

__all__ = [
    "ClientState",
    "FubonAPIClient",
    "FubonCredentials",
    "StreamingDataClient",
    "StreamingCredentials",
    "Subscription",
]
