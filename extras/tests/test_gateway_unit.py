import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.market import MarketAPI
from vnpy_fubon.normalization import normalize_exchange, normalize_product, normalize_symbol
from vnpy_fubon.order import OrderAPI
from vnpy_fubon.vnpy_compat import (
    AccountData,
    Direction,
    EVENT_CONTRACT,
    EVENT_ACCOUNT,
    EVENT_FUBON_MARKET_RAW,
    EVENT_LOG,
    EVENT_POSITION,
    EVENT_TICK,
    Exchange,
    Product,
    OrderRequest,
    OrderType,
    PositionData,
)


class DummyClient:
    pass


class DummyEventEngine:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


def test_normalization_symbol_and_product():
    assert normalize_symbol("txf88 ") == "TXF88"
    assert normalize_symbol("   Txo13800l5") == "TXO13800L5"

    assert normalize_product("future").name == Product.FUTURES.name  # type: ignore[attr-defined]
    assert normalize_product("options").name == Product.OPTION.name  # type: ignore[attr-defined]


def test_normalization_exchange_aliases():
    taifex = normalize_exchange("taifex")
    assert getattr(taifex, "value", str(taifex)) == "CFE"

    twse = normalize_exchange("TWSE")
    assert getattr(twse, "value", str(twse)) in {"TSE", "TWSE"}

    otc = normalize_exchange("tpex")
    assert getattr(otc, "value", str(otc)) == "OTC"

def test_market_parse_websocket_single_entry():
    api = MarketAPI(DummyClient())
    message = json.dumps(
        {
            "data": {
                "symbol": "TXFA4",
                "exchange": "TWSE",
                "name": "TXF",
                "price": 20500,
                "volume": 10,
                "timestamp": "2025-10-15 08:45:00",
            }
        }
    )
    items = api.parse_websocket_message(message)
    assert len(items) == 1
    tick, raw = items[0]
    assert tick.symbol == "TXFA4"
    assert raw["price"] == 20500


def test_market_parse_websocket_list_entries():
    api = MarketAPI(DummyClient())
    payload = {
        "data": [
            {"symbol": "2330", "exchange": "TWSE", "price": 600},
            {"symbol": "2454", "exchange": "TPEx", "price": 850},
        ]
    }
    message = json.dumps(payload)
    items = api.parse_websocket_message(message)
    assert len(items) == 2
    symbols = {tick.symbol for tick, _ in items}
    assert symbols == {"2330", "2454"}


def test_market_parse_websocket_invalid_json():
    api = MarketAPI(DummyClient())
    items = api.parse_websocket_message("not json")
    assert items == []


def test_order_status_fallback():
    api = OrderAPI(DummyClient(), gateway_name="TEST")
    payload = {
        "status": "UNEXPECTED_CODE",
        "symbol": "ABC",
        "side": "BUY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    req = {"symbol": "ABC", "side": "BUY", "price": 1, "quantity": 1}
    order = api.to_order_data(payload, req)
    assert order.status.name in {"SUBMITTING", "NOTTRADED"}


def test_order_direction_fallback():
    api = OrderAPI(DummyClient(), gateway_name="TEST")
    payload = {
        "status": "FILLED",
        "symbol": "ABC",
        "side": "WEIRD",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    req = {"symbol": "ABC", "side": "BUY", "price": 1, "quantity": 1}
    order = api.to_order_data(payload, req)
    assert order.direction in {Direction.LONG, Direction.SHORT}


def test_order_api_public_helpers():
    api = OrderAPI(DummyClient(), gateway_name="TEST")
    req = OrderRequest(
        symbol="ABC",
        exchange=Exchange.TWSE,  # type: ignore[arg-type]
        direction=Direction.LONG,
        price=Decimal("1"),
        volume=Decimal("1"),
        type=OrderType.LIMIT,
    )
    payload = api._build_order_payload(req)
    order = api.to_order_data({"symbol": "ABC"}, payload)
    assert order.symbol == "ABC"


def test_gateway_subscribe_error_triggers_reconnect():
    class StubWebSocket:
        def __init__(self) -> None:
            self.connected = False

        def connect(self) -> None:
            self.connected = True

        def on(self, *_args, **_kwargs) -> None:
            pass

        def off(self, *_args, **_kwargs) -> None:
            pass

        def subscribe(self, _payload) -> None:
            raise ValueError("invalid channel")

        def unsubscribe(self, _payload) -> None:
            pass

        def disconnect(self) -> None:
            pass

    class StubFactory:
        def __init__(self, ws: StubWebSocket) -> None:
            self._ws = ws

        @property
        def futopt(self) -> StubWebSocket:
            return self._ws

    class StubClient:
        def __init__(self) -> None:
            self.login_response = type("LoginResponse", (), {"data": [type("Acct", (), {"account": "001"})()]})()
            self.marketdata = type("MD", (), {})()
            self.marketdata.websocket_client = StubFactory(StubWebSocket())

        def exchange_realtime_token(self) -> None:
            pass

        def init_realtime(self) -> None:
            pass

    client = StubClient()
    engine = DummyEventEngine()
    gateway = FubonGateway(engine, client=client)
    gateway._start_token_refresh = lambda: None  # avoid timers
    gateway._cancel_token_refresh = lambda: None

    # prevent actual reconnect timers
    flag = {"called": False}

    def fake_schedule() -> None:
        flag["called"] = True

    gateway._schedule_ws_reconnect = fake_schedule

    gateway.connect({})

    with pytest.raises(ValueError):
        gateway.subscribe_quotes(["INVALID"], channels=["trades"])

        assert not flag["called"]
        log_events = [event for event in engine.events if event.type == EVENT_LOG]
        assert log_events
        messages = [getattr(event.data, "msg", event.data) for event in log_events]
        assert any("Subscription rejected" in message for message in messages)
    gateway.close()


def test_market_parse_market_events_trades():
    api = MarketAPI(DummyClient())
    message = json.dumps(
        {"channel": "trades", "data": {"symbol": "2330", "price": 600, "quantity": 1}}
    )
    events = api.parse_market_events(message)
    assert len(events) == 1
    event = events[0]
    assert event.channel == "trades"
    assert event.event_type == "trade"
    assert event.tick is None
    assert api.parse_websocket_message(message) == []


def test_gateway_market_message_dispatch_emits_raw_and_tick():
    engine = DummyEventEngine()
    gateway = FubonGateway(engine, client=DummyClient())
    gateway.market_api = MarketAPI(DummyClient())
    message = json.dumps(
        {
            "channel": "books",
            "data": {
                "symbol": "TXFA4",
                "exchange": "TWSE",
                "name": "TXF",
                "price": 20500,
                "volume": 10,
                "bid_price": 20499,
                "bid_volume": 5,
                "ask_price": 20501,
                "ask_volume": 3,
            },
        }
    )
    gateway._handle_ws_message(message)
    raw_events = [event for event in engine.events if event.type == EVENT_FUBON_MARKET_RAW]
    assert raw_events
    assert raw_events[-1].data["channel"] == "books"
    assert raw_events[-1].data["event_type"] == "orderbook"
    tick_events = [event for event in engine.events if event.type == EVENT_TICK]
    assert tick_events
    assert tick_events[-1].data.symbol == "TXFA4"


def test_gateway_market_trade_message_only_emits_raw():
    engine = DummyEventEngine()
    gateway = FubonGateway(engine, client=DummyClient())
    gateway.market_api = MarketAPI(DummyClient())
    message = json.dumps(
        {"channel": "trades", "data": {"symbol": "2330", "price": 600, "quantity": 1}}
    )
    gateway._handle_ws_message(message)
    raw_events = [event for event in engine.events if event.type == EVENT_FUBON_MARKET_RAW]
    assert raw_events
    assert raw_events[-1].data["event_type"] == "trade"
    tick_events = [event for event in engine.events if event.type == EVENT_TICK]
    assert not tick_events


def test_query_account_dispatches_event():
    engine = DummyEventEngine()
    gateway = FubonGateway(engine, client=DummyClient())
    account = AccountData(
        accountid="001",
        balance=Decimal("100"),
        frozen=Decimal("0"),
        available=Decimal("100"),
        currency="TWD",
        gateway_name="Fubon",
        timestamp=datetime.now(timezone.utc),
    )

    class AccountAPIStub:
        def query_account(self_inner):
            return account

        def query_positions(self_inner):
            return []

    gateway.account_api = AccountAPIStub()
    result = gateway.query_account()
    assert result == account
    account_events = [event for event in engine.events if event.type == EVENT_ACCOUNT]
    assert account_events
    assert account_events[-1].data == account


def test_query_positions_dispatches_events():
    engine = DummyEventEngine()
    gateway = FubonGateway(engine, client=DummyClient())
    position = PositionData(
        symbol="TXFA4",
        exchange=Exchange.TWSE,  # type: ignore[arg-type]
        direction=Direction.LONG,
        volume=Decimal("1"),
        frozen=Decimal("0"),
        price=Decimal("20000"),
        pnl=Decimal("0"),
        yd_volume=Decimal("0"),
        gateway_name="Fubon",
        timestamp=datetime.now(timezone.utc),
    )

    class AccountAPIStub:
        def query_account(self_inner):
            return None

        def query_positions(self_inner):
            return [position]

    gateway.account_api = AccountAPIStub()
    result = gateway.query_positions()
    assert result == [position]
    position_events = [event for event in engine.events if event.type == EVENT_POSITION]
    assert position_events
    assert position_events[-1].data == position


def test_gateway_account_metadata_extraction():
    engine = DummyEventEngine()

    class AccountObj:
        def __init__(self, account: str, name: str, default: bool = False) -> None:
            self.account = account
            self.account_name = name
            self.account_type = "futures" if default else "cash"
            self.default = default

    login_response = type(
        "LoginResponse",
        (),
        {
            "data": [
                AccountObj("001", "Primary", default=True),
                {"account": "002", "name": "Secondary", "type": "cash"},
            ]
        },
    )()

    gateway = FubonGateway(engine, client=DummyClient())
    gateway.login_response = login_response
    gateway._populate_account_metadata({})

    metadata = gateway.get_account_metadata()
    assert len(metadata) == 2
    indexed = {item["account_id"]: item for item in metadata}
    assert indexed["001"]["account_name"] == "Primary"
    assert indexed["001"]["is_default"] is True
    assert gateway.get_available_accounts() == ["001", "002"]


def test_switch_account_validation_success():
    engine = DummyEventEngine()

    class StubClient:
        def __init__(self) -> None:
            self._current_account = "001"
            self.login_response = type(
                "LoginResponse",
                (),
                {
                    "data": [
                        type("Acct", (), {"account": "001"})(),
                        type("Acct", (), {"account": "002"})(),
                    ]
                },
            )()

        def set_current_account(self, account_id: str) -> None:
            self._current_account = account_id

        def get_current_account(self):
            return {"account": self._current_account}

    client = StubClient()
    gateway = FubonGateway(engine, client=client)
    gateway.login_response = client.login_response
    gateway._populate_account_metadata({})
    gateway.order_api = type("OrderAPIStub", (), {"account_id": None})()

    assert gateway.switch_account("002") is True
    assert gateway.order_api.account_id == "002"
    assert gateway.primary_account_id == "002"


def test_switch_account_validation_failure_emits_log():
    engine = DummyEventEngine()

    class StubClient:
        def __init__(self) -> None:
            self._current_account = "001"
            self.login_response = type(
                "LoginResponse",
                (),
                {
                    "data": [
                        type("Acct", (), {"account": "001"})(),
                        type("Acct", (), {"account": "002"})(),
                    ]
                },
            )()

        def set_current_account(self, account_id: str) -> None:
            # intentionally refuse to switch underlying SDK state
            _ = account_id

        def get_current_account(self):
            return {"account": self._current_account}

    client = StubClient()
    gateway = FubonGateway(engine, client=client)
    gateway.login_response = client.login_response
    gateway._populate_account_metadata({})
    gateway.order_api = type("OrderAPIStub", (), {"account_id": None})()

    assert gateway.switch_account("002") is False
    log_events = [event for event in engine.events if event.type == EVENT_LOG]
    assert log_events
    messages = [getattr(event.data, "msg", event.data) for event in log_events]
    assert any("SDK reports active account" in message for message in messages)
    assert gateway.primary_account_id == "001"
    assert gateway.order_api.account_id is None


def test_contract_loading_publishes_events():
    engine = DummyEventEngine()

    class StubIntraday:
        def products(self, **kwargs):
            instrument_type = kwargs.get("type", "FUTURE").upper()
            if "OPTION" in instrument_type:
                return {
                    "data": [
                        {
                            "symbol": "TXO",
                            "type": "OPTION",
                            "contractSize": 50,
                            "name": "TXO Options",
                            "underlyingSymbol": "TXF",
                        }
                    ]
                }
            return {
                "data": [
                    {
                        "symbol": "TXF",
                        "type": "FUTURE",
                        "contractSize": 200,
                        "name": "TXF Futures",
                    }
                ]
            }

        def tickers(self, **kwargs):
            instrument_type = kwargs.get("type", "FUTURE").upper()
            if "OPTION" in instrument_type:
                return {
                    "data": [
                        {
                            "symbol": "TXO13800L5",
                            "type": "OPTION",
                            "name": "TXO 13800",
                            "settlementDate": "2025-12-17",
                            "startDate": "2025-07-22",
                            "exchange": "TAIFEX",
                        }
                    ]
                }
            return {
                "data": [
                    {
                        "symbol": "TXFC6",
                        "type": "FUTURE",
                        "name": "TXF 2026-03",
                        "settlementDate": "2026-03-18",
                        "startDate": "2025-03-20",
                        "exchange": "TAIFEX",
                    }
                ]
            }

    class StubFutOptRest:
        def __init__(self) -> None:
            self.intraday = StubIntraday()

    class StubRestClient:
        def __init__(self) -> None:
            self.futopt = StubFutOptRest()

    class StubMarketData:
        def __init__(self) -> None:
            self.rest_client = StubRestClient()

    class StubGatewayClient:
        def __init__(self) -> None:
            self.marketdata = StubMarketData()

    gateway = FubonGateway(engine, client=StubGatewayClient())
    gateway._load_and_publish_contracts()

    contract_events = [event for event in engine.events if event.type == EVENT_CONTRACT]
    assert contract_events, "Expected contract events to be published"
    vt_symbols = {contract.vt_symbol for contract in gateway.contracts.values()}
    assert any(symbol.startswith("TXFC6") for symbol in vt_symbols)
    assert any(symbol.startswith("TXO13800") for symbol in vt_symbols)
