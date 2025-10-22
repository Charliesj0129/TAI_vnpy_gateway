"""
Mapping tables between Fubon SDK codes and vn.py enums.
"""

from __future__ import annotations

from typing import Dict

from .vnpy_compat import Direction, OrderStatus, OrderType

# Fubon side order types mapped to vn.py OrderType
ORDER_TYPE_MAP: Dict[str, OrderType] = {
    "ROD": OrderType.LIMIT,
    "IOC": getattr(OrderType, "IOC", OrderType.MARKET),  # fallback to MARKET if IOC missing
    "FOK": getattr(OrderType, "FOK", OrderType.MARKET),
    "LIMIT": OrderType.LIMIT,
    "MARKET": OrderType.MARKET,
}

# Reverse mapping for outbound order requests
ORDER_TYPE_REVERSE_MAP: Dict[OrderType, str] = {
    OrderType.LIMIT: "ROD",
    getattr(OrderType, "IOC", OrderType.LIMIT): "IOC",
    getattr(OrderType, "FOK", OrderType.LIMIT): "FOK",
    OrderType.MARKET: "MARKET",
}

# Direction mapping
DIRECTION_MAP: Dict[str, Direction] = {
    "BUY": Direction.LONG,
    "SELL": Direction.SHORT,
    "LONG": Direction.LONG,
    "SHORT": Direction.SHORT,
}

DIRECTION_REVERSE_MAP: Dict[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL",
}

# Order status mapping
ORDER_STATUS_MAP: Dict[str, OrderStatus] = {
    "NEW": getattr(OrderStatus, "NOTTRADED", OrderStatus.SUBMITTING),
    "ACCEPTED": getattr(OrderStatus, "NOTTRADED", OrderStatus.SUBMITTING),
    "PARTIALLY_FILLED": getattr(OrderStatus, "PARTTRADED", OrderStatus.SUBMITTING),
    "PARTIAL_FILLED": getattr(OrderStatus, "PARTTRADED", OrderStatus.SUBMITTING),
    "PART_FILL": getattr(OrderStatus, "PARTTRADED", OrderStatus.SUBMITTING),
    "FILLED": getattr(OrderStatus, "ALLTRADED", OrderStatus.ALLTRADED),
    "DEAL": getattr(OrderStatus, "ALLTRADED", OrderStatus.ALLTRADED),
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.CANCELLED,
    "PENDING": getattr(OrderStatus, "NOTTRADED", OrderStatus.SUBMITTING),
    "PENDING_CANCEL": getattr(OrderStatus, "CANCELLED", OrderStatus.CANCELLED),
    "SENT": getattr(OrderStatus, "SUBMITTING", OrderStatus.NOTTRADED),
    "QUEUED": getattr(OrderStatus, "NOTTRADED", OrderStatus.SUBMITTING),
}

__all__ = [
    "DIRECTION_MAP",
    "DIRECTION_REVERSE_MAP",
    "ORDER_STATUS_MAP",
    "ORDER_TYPE_MAP",
    "ORDER_TYPE_REVERSE_MAP",
]
