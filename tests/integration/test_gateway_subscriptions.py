
from __future__ import annotations

from typing import Any, Dict, Tuple

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.normalization import normalize_symbol
from vnpy_fubon.vnpy_compat import EVENT_LOG


class RecordingEventEngine:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


class StubWebsocketClient:
    def __init__(self) -> None:
        self.connected = False
        self.subscribed: list[Dict[str, Any]] = []
        self.unsubscribed: list[Dict[str, Any]] = []
        self.handlers: Dict[str, Any] = {}

    def connect(self) -> None:
        self.connected = True

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def off(self, event: str, handler) -> None:
        self.handlers.pop(event, None)

    def subscribe(self, payload: Dict[str, Any]) -> None:
        self.subscribed.append(dict(payload))

    def unsubscribe(self, payload: Dict[str, Any]) -> None:
        self.unsubscribed.append(dict(payload))

    def disconnect(self) -> None:
        self.connected = False


def _prepare_gateway_with_stub_ws() -> Tuple[FubonGateway, StubWebsocketClient, RecordingEventEngine]:
    engine = RecordingEventEngine()
    gateway = FubonGateway(engine)
    # Avoid actual network activity
    websocket = StubWebsocketClient()
    gateway.client = object()
    gateway._ws_client = websocket
    gateway._ws_connected = True
    gateway._ws_handlers_registered = True
    gateway._active_subscriptions.clear()
    return gateway, websocket, engine


def test_subscription_batching_and_idempotency() -> None:
    gateway, websocket, _engine = _prepare_gateway_with_stub_ws()

    symbols = [f"TXF{i:03d}" for i in range(100)]
    gateway.subscribe_quotes(symbols, channels=("books",))

    assert len(websocket.subscribed) == len(symbols)
    assert len(gateway._active_subscriptions) == len(symbols)

    # Re-subscribing should not generate duplicate websocket requests
    gateway.subscribe_quotes(symbols, channels=("books",))
    assert len(websocket.subscribed) == len(symbols)

    # Ensure subscription keys include channel and symbol
    keys = {(item["channel"], item["symbol"]) for item in websocket.subscribed}
    assert len(keys) == len(symbols)


def test_unsubscribe_clears_active_registry() -> None:
    gateway, websocket, engine = _prepare_gateway_with_stub_ws()

    payloads = [{"channel": "books", "symbol": f"TXO{i:03d}"} for i in range(5)]
    gateway._active_subscriptions = {(item["channel"], item["symbol"], None) for item in payloads}

    for item in payloads:
        gateway._ws_client.unsubscribe(item)
        gateway._active_subscriptions.discard((item["channel"], item["symbol"], None))

    assert not gateway._active_subscriptions
    assert len(websocket.unsubscribed) == len(payloads)

    log_messages = [
        getattr(event.data, "msg", event.data)
        for event in engine.events
        if event.type == EVENT_LOG
    ]
    assert all("Subscribed websocket" not in message for message in log_messages)


def test_close_resets_subscription_state() -> None:
    gateway, websocket, _engine = _prepare_gateway_with_stub_ws()
    gateway.contracts["TXFQ4.CFE"] = object()  # sentinel
    gateway._symbol_aliases["TXFQ4"] = "TXFQ4.CFE"
    gateway._symbol_exchange_aliases[(normalize_symbol("TXFQ4"), "CFE")] = "TXFQ4.CFE"

    gateway.close()

    assert not gateway._active_subscriptions
    assert not gateway.contracts
    assert not gateway._symbol_aliases
    assert not gateway._symbol_exchange_aliases
    assert not websocket.connected
