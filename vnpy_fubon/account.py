"""
Thin wrapper around the Fubon SDK account endpoints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Mapping, Optional

from .exceptions import FubonSDKMethodNotFoundError
from .vnpy_compat import AccountData, Direction, Exchange, PositionData

ACCOUNT_QUERY_METHODS = ("query_account", "get_account_info", "QueryAccount")
POSITION_QUERY_METHODS = ("query_positions", "get_positions", "QueryPositions")
BALANCE_QUERY_METHODS = ("query_balances", "get_balance", "QueryBalance")

LOGGER = logging.getLogger("vnpy_fubon.account")


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
    if isinstance(raw, Exchange):
        return raw
    if not isinstance(raw, str):
        return getattr(Exchange, "UNKNOWN", Exchange("UNKNOWN"))  # type: ignore[arg-type]
    upper = raw.upper()
    if upper in {"TWSE", "TW"}:
        return getattr(Exchange, "TWSE", Exchange("TWSE"))  # type: ignore[arg-type]
    if upper in {"TPEX", "OTC"}:
        return getattr(Exchange, "TPEx", Exchange("TPEx"))  # type: ignore[arg-type]
    try:
        return Exchange(upper)  # type: ignore[arg-type]
    except Exception:
        return getattr(Exchange, "UNKNOWN", Exchange("UNKNOWN"))  # type: ignore[arg-type]


def _normalise_direction(raw: Any) -> Direction:
    if isinstance(raw, Direction):
        return raw
    if isinstance(raw, str):
        upper = raw.upper()
        if upper in {"BUY", "LONG"}:
            return getattr(Direction, "LONG", Direction("long"))  # type: ignore[arg-type]
        if upper in {"SELL", "SHORT"}:
            return getattr(Direction, "SHORT", Direction("short"))  # type: ignore[arg-type]
    return getattr(Direction, "NET", Direction("net"))  # type: ignore[arg-type]


def _extract_list(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


@dataclass
class AccountSnapshot:
    """
    Bundle of account and position information returned by the SDK.
    """

    account: AccountData
    positions: List[PositionData]


class AccountAPI:
    """
    Facade over account-related SDK calls with conversion to vn.py data classes.
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

    def query_account(self, **kwargs: Any) -> AccountData:
        method = self._resolve_method(ACCOUNT_QUERY_METHODS)
        response = method(**kwargs)
        return self._to_account_data(response)

    def query_positions(self, **kwargs: Any) -> List[PositionData]:
        method = self._resolve_method(POSITION_QUERY_METHODS)
        response = method(**kwargs)
        positions_raw = _extract_list(response)
        return [self._to_position_data(item) for item in positions_raw]

    def query_balances(self, **kwargs: Any) -> AccountData:
        method = self._resolve_method(BALANCE_QUERY_METHODS)
        response = method(**kwargs)
        return self._to_account_data(response)

    def snapshot(self) -> AccountSnapshot:
        """
        Convenience helper returning both account info and positions in one call.
        """

        account = self.query_account()
        positions = self.query_positions()
        return AccountSnapshot(account=account, positions=positions)

    def _resolve_method(self, candidates: Iterable[str]) -> Any:
        for name in candidates:
            method = getattr(self.client, name, None)
            if callable(method):
                return method
        raise FubonSDKMethodNotFoundError(
            f"AccountAPI cannot find any of {candidates} on client {type(self.client).__name__}"
        )

    def _to_account_data(self, payload: Any) -> AccountData:
        if not isinstance(payload, Mapping):
            self.logger.debug("Unexpected account payload type %s", type(payload))
            return AccountData(
                accountid="UNKNOWN",
                balance=Decimal("0"),
                frozen=Decimal("0"),
                available=Decimal("0"),
                currency="TWD",
                gateway_name=self.gateway_name,
                timestamp=datetime.utcnow(),
            )

        account_id = str(
            payload.get("account")
            or payload.get("account_id")
            or payload.get("acct")
            or payload.get("user_id")
            or "UNKNOWN"
        )
        balance = (
            payload.get("equity")
            or payload.get("balance")
            or payload.get("cash")
            or payload.get("total_asset")
        )
        available = payload.get("available") or payload.get("cash_available") or balance
        frozen = payload.get("frozen") or payload.get("hold") or 0

        timestamp_raw = payload.get("timestamp") or payload.get("update_time")
        timestamp = datetime.utcnow()
        if isinstance(timestamp_raw, datetime):
            timestamp = timestamp_raw

        account_data = AccountData(
            accountid=account_id,
            balance=_ensure_decimal(balance),
            frozen=_ensure_decimal(frozen),
            available=_ensure_decimal(available),
            currency=str(payload.get("currency") or "TWD"),
            gateway_name=self.gateway_name,
            timestamp=timestamp,
        )

        self.logger.debug("Mapped account payload %s to %s", payload, account_data)
        return account_data

    def _to_position_data(self, payload: Mapping[str, Any]) -> PositionData:
        symbol = str(payload.get("symbol") or payload.get("code") or "")
        exchange = _normalize_exchange(payload.get("exchange"))
        direction = _normalise_direction(payload.get("direction") or payload.get("side"))
        volume = _ensure_decimal(payload.get("volume") or payload.get("qty") or 0)
        frozen = _ensure_decimal(payload.get("frozen") or payload.get("hold") or 0)
        price = _ensure_decimal(payload.get("avg_price") or payload.get("price") or 0)
        pnl = _ensure_decimal(payload.get("unrealized_pnl") or payload.get("pnl") or 0)
        yd_volume = _ensure_decimal(payload.get("yd_volume") or payload.get("yesterday_volume") or 0)

        timestamp_raw = payload.get("timestamp") or payload.get("update_time")
        timestamp = datetime.utcnow()
        if isinstance(timestamp_raw, datetime):
            timestamp = timestamp_raw

        position = PositionData(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            volume=volume,
            frozen=frozen,
            price=price,
            pnl=pnl,
            yd_volume=yd_volume,
            gateway_name=self.gateway_name,
            timestamp=timestamp,
        )
        self.logger.debug("Mapped position payload %s to %s", payload, position)
        return position

