"""
Compatibility layer to interact with vn.py's data structures and constants.

If the real vn.py package is unavailable (e.g. during local development or CI),
the module provides light-weight stand-ins that mimic the expected interface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Optional

try:  # pragma: no cover - exercised only when vn.py is installed
    from vnpy.event import Event
    from vnpy.trader.constant import (
        Direction,
        Exchange,
        Offset,
        OrderStatus,
        OrderType,
        Product,
        OptionType,
        Interval,
    )
    from vnpy.trader.event import (
        EVENT_ACCOUNT,
        EVENT_CONTRACT,
        EVENT_LOG,
        EVENT_ORDER,
        EVENT_POSITION,
        EVENT_TICK,
        EVENT_TRADE,
    )
    try:  # pragma: no cover - only executed when vn.py exposes custom event
        from vnpy.trader.event import EVENT_FUBON_MARKET_RAW  # type: ignore
    except ImportError:  # pragma: no cover - vn.py without custom event
        EVENT_FUBON_MARKET_RAW = "eFubonMarketRaw"
    from vnpy.trader.gateway import BaseGateway
    from vnpy.trader.object import (
        AccountData,
        ContractData,
        OrderData,
        OrderRequest,
        PositionData,
        TickData,
        TradeData,
        BarData,
        HistoryRequest,
    )
except ImportError:  # pragma: no cover - default for most development environments

    class Direction(str, Enum):
        LONG = "long"
        SHORT = "short"
        NET = "net"


    class Offset(str, Enum):
        NONE = "none"
        OPEN = "open"
        CLOSE = "close"
        CLOSETODAY = "close_today"
        CLOSEYESTERDAY = "close_yesterday"


    class OrderType(str, Enum):
        LIMIT = "limit"
        MARKET = "market"
        IOC = "ioc"
        FOK = "fok"


    class OrderStatus(str, Enum):
        SUBMITTING = "submitting"
        NOTTRADED = "not_traded"
        PARTTRADED = "part_traded"
        ALLTRADED = "all_traded"
        REJECTED = "rejected"
        CANCELLED = "cancelled"


    class Exchange(str, Enum):
        UNKNOWN = "UNKNOWN"
        TWSE = "TWSE"
        TPEx = "TPEx"
        TSE = "TSE"
        OTC = "OTC"
        CFE = "CFE"
        CME = "CME"
        SGX = "SGX"
        HKFE = "HKFE"
        LOCAL = "LOCAL"
        GLOBAL = "GLOBAL"


    class Product(str, Enum):
        EQUITY = "EQUITY"
        FUTURES = "FUTURES"
        OPTION = "OPTION"
        INDEX = "INDEX"
        FOREX = "FOREX"
        SPOT = "SPOT"
        ETF = "ETF"
        BOND = "BOND"
        WARRANT = "WARRANT"
        SPREAD = "SPREAD"
        FUND = "FUND"
        CFD = "CFD"
        SWAP = "SWAP"


    class OptionType(str, Enum):
        CALL = "call"
        PUT = "put"

    class Interval(str, Enum):
        MINUTE = "1m"
        HOUR = "1h"
        DAILY = "d"
        WEEKLY = "w"
        TICK = "tick"


    @dataclass
    class AccountData:
        accountid: str
        balance: Decimal
        frozen: Decimal
        available: Decimal
        currency: str
        gateway_name: str
        timestamp: Optional[datetime] = None
        yesterday_balance: Decimal = Decimal("0")
        today_balance: Decimal = Decimal("0")
        today_equity: Decimal = Decimal("0")
        initial_margin: Decimal = Decimal("0")
        maintenance_margin: Decimal = Decimal("0")
        clearing_margin: Decimal = Decimal("0")
        excess_margin: Decimal = Decimal("0")
        available_margin: Decimal = Decimal("0")
        disgorgement: Decimal = Decimal("0")
        fut_realized_pnl: Decimal = Decimal("0")
        fut_unrealized_pnl: Decimal = Decimal("0")
        opt_value: Decimal = Decimal("0")
        opt_long_value: Decimal = Decimal("0")
        opt_short_value: Decimal = Decimal("0")
        opt_pnl: Decimal = Decimal("0")
        today_fee: Decimal = Decimal("0")
        today_tax: Decimal = Decimal("0")
        today_cash_in: Decimal = Decimal("0")
        today_cash_out: Decimal = Decimal("0")
        extra: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class EquityData:
        accountid: str
        currency: str
        today_equity: Decimal
        yesterday_balance: Decimal
        today_balance: Decimal
        initial_margin: Decimal
        maintenance_margin: Decimal
        clearing_margin: Decimal
        excess_margin: Decimal
        available_margin: Decimal
        disgorgement: Decimal
        fut_realized_pnl: Decimal
        fut_unrealized_pnl: Decimal
        opt_value: Decimal
        opt_long_value: Decimal
        opt_short_value: Decimal
        opt_pnl: Decimal
        today_fee: Decimal
        today_tax: Decimal
        today_cash_in: Decimal
        today_cash_out: Decimal
        timestamp: Optional[datetime] = None
        extra: Dict[str, Any] = field(default_factory=dict)


    @dataclass
    class PositionData:
        symbol: str
        exchange: Exchange
        direction: Direction
        volume: Decimal
        frozen: Decimal
        price: Decimal
        pnl: Decimal
        yd_volume: Decimal
        gateway_name: str
        timestamp: Optional[datetime] = None


    @dataclass
    class OrderRequest:
        symbol: str
        exchange: Exchange
        direction: Direction
        price: Decimal
        volume: Decimal
        type: OrderType = OrderType.LIMIT
        offset: Offset = Offset.NONE


    @dataclass
    class OrderData:
        symbol: str
        exchange: Exchange
        orderid: str
        direction: Direction
        offset: Offset
        price: Decimal
        volume: Decimal
        traded: Decimal
        status: OrderStatus
        datetime: datetime
        gateway_name: str
        reference: Optional[str] = None


    @dataclass
    class TradeData:
        symbol: str
        exchange: Exchange
        tradeid: str
        orderid: str
        direction: Direction
        offset: Offset
        price: Decimal
        volume: Decimal
        datetime: datetime
        gateway_name: str


    @dataclass
    class TickData:
        symbol: str
        exchange: Exchange
        datetime: datetime
        name: str
        last_price: Decimal
        volume: Decimal
        bid_price_1: Optional[Decimal] = None
        bid_volume_1: Optional[Decimal] = None
        ask_price_1: Optional[Decimal] = None
        ask_volume_1: Optional[Decimal] = None
        gateway_name: str = "Fubon"

    @dataclass
    class ContractData:
        gateway_name: str
        symbol: str
        exchange: Exchange
        name: str
        product: Product
        size: float
        pricetick: float
        min_volume: float = 1
        max_volume: Optional[float] = None
        stop_supported: bool = False
        net_position: bool = False
        history_data: bool = False
        option_strike: Optional[float] = None
        option_underlying: Optional[str] = None
        option_type: Optional[OptionType] = None
        option_listed: Optional[datetime] = None
        option_expiry: Optional[datetime] = None
        option_portfolio: Optional[str] = None
        option_index: Optional[str] = None

        def __post_init__(self) -> None:
            exchange_value = getattr(self.exchange, "value", self.exchange)
            self.vt_symbol: str = f"{self.symbol}.{exchange_value}"

    @dataclass
    class BarData:
        gateway_name: str
        symbol: str
        exchange: Exchange
        datetime: datetime
        interval: Optional[Interval] = None
        volume: float = 0.0
        turnover: float = 0.0
        open_interest: float = 0.0
        open_price: float = 0.0
        high_price: float = 0.0
        low_price: float = 0.0
        close_price: float = 0.0

        def __post_init__(self) -> None:
            exchange_value = getattr(self.exchange, "value", self.exchange)
            self.vt_symbol: str = f"{self.symbol}.{exchange_value}"

    @dataclass
    class LogData:
        msg: str
        gateway_name: str
        level: int = logging.INFO

        def __post_init__(self) -> None:
            self.time: datetime = datetime.now()

    @dataclass
    class SubscribeRequest:
        symbol: str
        exchange: Exchange

    @dataclass
    class HistoryRequest:
        symbol: str
        exchange: Exchange
        start: datetime
        end: Optional[datetime] = None
        interval: Optional[Interval] = None

        def __post_init__(self) -> None:
            exchange_value = getattr(self.exchange, "value", self.exchange)
            self.vt_symbol: str = f"{self.symbol}.{exchange_value}"

    @dataclass
    class Event:
        type: str
        data: Any = None


    EVENT_TICK = "eTick."
    EVENT_ORDER = "eOrder."
    EVENT_CONTRACT = "eContract."
    EVENT_ACCOUNT = "eAccount."
    EVENT_POSITION = "ePosition."
    EVENT_TRADE = "eTrade."
    EVENT_FUBON_MARKET_RAW = "eFubonMarketRaw"
    EVENT_LOG = "eLog"


    class BaseGateway:
        """
        Minimal compatibility shim for vn.py BaseGateway.
        """

        def __init__(self, event_engine: Callable[[Event], None], gateway_name: str) -> None:
            self.event_engine = event_engine
            self.gateway_name = gateway_name
            self._logger = logging.getLogger(f"vnpy.gateway.{gateway_name.lower()}")

        def on_event(self, event_type: str, data: Any = None) -> None:
            event = Event(event_type, data)
            put = getattr(self.event_engine, "put", None)
            if callable(put):
                put(event)
            else:
                self.event_engine(event)

        def on_contract(self, contract: ContractData) -> None:
            self.on_event(EVENT_CONTRACT, contract)

        def on_log(self, message: str) -> None:
            self.on_event(EVENT_LOG, message)

        def write_log(self, message: str) -> None:
            self._logger.info(message)
            log = LogData(msg=message, gateway_name=self.gateway_name)
            self.on_log(log)


__all__ = [
    "AccountData",
    "BaseGateway",
    "ContractData",
    "Direction",
    "Exchange",
    "EquityData",
    "Event",
    "BarData",
    "LogData",
    "HistoryRequest",
    "EVENT_ACCOUNT",
    "EVENT_CONTRACT",
    "EVENT_LOG",
    "EVENT_FUBON_MARKET_RAW",
    "EVENT_ORDER",
    "EVENT_POSITION",
    "EVENT_TICK",
    "EVENT_TRADE",
    "Offset",
    "OptionType",
    "OrderData",
    "OrderRequest",
    "OrderStatus",
    "OrderType",
    "PositionData",
    "Product",
    "Interval",
    "SubscribeRequest",
    "TickData",
    "TradeData",
]
