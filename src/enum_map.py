"""
Enum Mapping Module for VnPy and Fubon Neo API Integration

This module provides bidirectional mappings between VnPy constants and Fubon Neo API constants.
These mappings ensure correct translation of trading concepts like direction, order type, and market type
between the two systems, facilitating seamless integration.

Mappings are defined as dictionaries with forward and reverse lookups for ease of use in both
request construction (VnPy -> Fubon) and response processing (Fubon -> VnPy).
"""

# --- VnPy Imports ---
from vnpy.trader.constant import Direction, OrderType, Offset, Exchange, Product, Status, OptionType

# --- Fubon Neo Imports ---
from fubon_neo.constant import (
    BSAction as FubonBSAction,
    PriceType as FubonPriceType,
    FutOptOrderType as FubonFutOptOrderType,
    FutOptPriceType as FubonFutOptPriceType,
    FutOptMarketType as FubonFutOptMarketType,
    CallPut as FubonCallPut,
    MarketType as FubonMarketType,
    TimeInForce as FubonTimeInForce,
)

# --- Direction Mapping (VnPy Direction <-> Fubon BSAction) ---
DIRECTION_MAP = {
    Direction.LONG: FubonBSAction.Buy,
    Direction.SHORT: FubonBSAction.Sell,
}
DIRECTION_MAP_REVERSE = {
    str(FubonBSAction.Buy): Direction.LONG,
    str(FubonBSAction.Sell): Direction.SHORT,
}

# --- Price Type Mapping (VnPy OrderType <-> Fubon PriceType) ---
PRICE_TYPE_MAP = {
    OrderType.LIMIT: (FubonPriceType.Limit, FubonTimeInForce.ROD),
    OrderType.MARKET: (FubonPriceType.Market, FubonTimeInForce.ROD),
    OrderType.FAK: (FubonPriceType.Market, FubonTimeInForce.IOC),
    OrderType.FOK: (FubonPriceType.Market, FubonTimeInForce.FOK),
}
PRICE_TYPE_MAP_REVERSE = {
    str((FubonPriceType.Limit, FubonTimeInForce.ROD)): OrderType.LIMIT,
    str((FubonPriceType.Market, FubonTimeInForce.ROD)): OrderType.MARKET,
    str((FubonPriceType.Market, FubonTimeInForce.IOC)): OrderType.FAK,
    str((FubonPriceType.Market, FubonTimeInForce.FOK)): OrderType.FOK,
}

# --- Futures/Options Price Type Mapping (VnPy OrderType <-> Fubon FutOptPriceType) ---
FUTOPT_PRICE_TYPE_MAP = {
    OrderType.LIMIT: (FubonFutOptPriceType.Limit, FubonTimeInForce.ROD),
    OrderType.MARKET: (FubonFutOptPriceType.Market, FubonTimeInForce.ROD),
    OrderType.FAK: (FubonFutOptPriceType.Market, FubonTimeInForce.IOC),
    OrderType.FOK: (FubonFutOptPriceType.Market, FubonTimeInForce.FOK),
}
FUTOPT_PRICE_TYPE_MAP_REVERSE = {
    str((FubonFutOptPriceType.Limit, FubonTimeInForce.ROD)): OrderType.LIMIT,
    str((FubonFutOptPriceType.Market, FubonTimeInForce.ROD)): OrderType.MARKET,
    str((FubonFutOptPriceType.Market, FubonTimeInForce.IOC)): OrderType.FAK,
    str((FubonFutOptPriceType.Market, FubonTimeInForce.FOK)): OrderType.FOK,
}

# --- Futures Offset Mapping (VnPy Offset <-> Fubon FutOptOrderType) ---
FUTURES_OFFSET_MAP = {
    Offset.OPEN: FubonFutOptOrderType.New,
    Offset.CLOSE: FubonFutOptOrderType.Close,
    Offset.NONE: FubonFutOptOrderType.Auto,
}
FUTURES_OFFSET_MAP_REVERSE = {
    str(FubonFutOptOrderType.New): Offset.OPEN,
    str(FubonFutOptOrderType.Close): Offset.CLOSE,
    str(FubonFutOptOrderType.Auto): Offset.NONE,
}

# --- Option Type Mapping (VnPy OptionType <-> Fubon CallPut) ---
OPTION_TYPE_MAP = {
    OptionType.CALL: FubonCallPut.Call,
    OptionType.PUT: FubonCallPut.Put,
}
OPTION_TYPE_MAP_REVERSE = {
    str(FubonCallPut.Call): OptionType.CALL,
    str(FubonCallPut.Put): OptionType.PUT,
}

# --- Market Type to Exchange Mapping (Fubon MarketType <-> VnPy Exchange) ---
MARKET_TYPE_EXCHANGE_MAP = {
    str(FubonMarketType.Common): Exchange.TWSE,
    str(FubonMarketType.Odd): Exchange.TWSE,
    str(FubonMarketType.IntradayOdd): Exchange.TWSE,
    str(FubonMarketType.Fixing): Exchange.TWSE,
    str(FubonMarketType.Emg): Exchange.TOTC,
    str(FubonMarketType.EmgOdd): Exchange.TOTC,
}

# --- Market Type to Product Mapping (Fubon MarketType <-> VnPy Product) ---
MARKET_TYPE_PRODUCT_MAP = {
    str(FubonMarketType.Common): Product.EQUITY,
    str(FubonMarketType.Odd): Product.EQUITY,
    str(FubonMarketType.IntradayOdd): Product.EQUITY,
    str(FubonMarketType.Fixing): Product.EQUITY,
    str(FubonMarketType.Emg): Product.EQUITY,
    str(FubonMarketType.EmgOdd): Product.EQUITY,
}

# --- Futures/Options Market Type Mapping (Fubon FutOptMarketType <-> VnPy Exchange) ---
FUTOPT_MARKET_TYPE_MAP = {
    str(FubonFutOptMarketType.Future): Exchange.TAIFEX,
    str(FubonFutOptMarketType.FutureNight): Exchange.TAIFEX,
    str(FubonFutOptMarketType.Option): Exchange.TAIFEX,
}

# --- Order Status Mapping (Fubon Status String <-> VnPy Status) ---
STATUS_MAP = {
    "Filled": Status.ALLTRADED,
    "PartFilled": Status.PARTTRADED,
    "Pending": Status.SUBMITTING,
    "Submitted": Status.NOTTRADED,
    "Cancelled": Status.CANCELLED,
    "Failed": Status.REJECTED,
}
