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
from .vnpy_compat import (
    AccountData,
    ClosePositionRecord,
    Direction,
    EquityData,
    Exchange,
    PositionData,
)

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


def _first_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            value = payload[key]
            if value not in (None, ""):
                return value
    return None


def _extract_position_entries(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if data is not None:
            return _extract_list(data)
    return _extract_list(payload)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return datetime.utcnow()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            formats = (
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%Y%m%d%H%M%S",
                "%Y-%m-%d",
            )
            for fmt in formats:
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return datetime.utcnow()


def _resolve_account_id(source: Any) -> Optional[str]:
    if isinstance(source, Mapping):
        value = _first_value(source, "account", "account_id", "acct", "id", "accountId")
        if value is not None:
            return str(value)
    for attr in ("account", "account_id", "acct", "id", "accountId"):
        try:
            value = getattr(source, attr, None)
        except Exception:
            value = None
        if value not in (None, ""):
            return str(value)
    return None


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

    def query_positions(self, *, account: Any = None, **kwargs: Any) -> List[PositionData]:
        accounting = getattr(self.client, "futopt_accounting", None)
        target_account = account or kwargs.get("account")

        if accounting is not None and target_account is not None:
            method = getattr(accounting, "query_hybrid_position", None) or getattr(
                accounting, "query_single_position", None
            )
            if callable(method):
                try:
                    response = method(target_account)
                    entries = _extract_position_entries(response)
                    return [self._to_position_data(item) for item in entries]
                except Exception as exc:
                    self.logger.debug("futopt_accounting position query failed: %s", exc, extra={"account": target_account})

        method = self._resolve_method(POSITION_QUERY_METHODS)
        response = method(**kwargs)
        positions_raw = _extract_list(response)
        return [self._to_position_data(item) for item in positions_raw]

    def query_balances(self, **kwargs: Any) -> AccountData:
        method = self._resolve_method(BALANCE_QUERY_METHODS)
        response = method(**kwargs)
        return self._to_account_data(response)

    def query_margin_equity(self, account: Any) -> List[EquityData]:
        accounting = getattr(self.client, "futopt_accounting", None)
        if accounting is None:
            raise FubonSDKMethodNotFoundError(
                f"Client {type(self.client).__name__} does not expose futopt_accounting module."
            )
        method = getattr(accounting, "query_margin_equity", None)
        if not callable(method):
            raise FubonSDKMethodNotFoundError(
                f"futopt_accounting on {type(self.client).__name__} has no query_margin_equity()."
            )

        account_id = _resolve_account_id(account) or "UNKNOWN"
        response = method(account)
        if isinstance(response, Mapping) and "data" in response:
            entries = _extract_list(response.get("data"))
        else:
            entries = _extract_list(response)

        equities: List[EquityData] = []
        for item in entries:
            try:
                equities.append(self._to_equity_data(item, account_id))
            except Exception as exc:
                self.logger.debug("Failed to map equity payload %s: %s", item, exc)
        return equities

    def query_close_position_records(
        self,
        account: Any,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> List[ClosePositionRecord]:
        accounting = getattr(self.client, "futopt_accounting", None)
        if accounting is None:
            raise FubonSDKMethodNotFoundError(
                f"Client {type(self.client).__name__} does not expose futopt_accounting module."
            )
        method = getattr(accounting, "close_position_record", None)
        if not callable(method):
            raise FubonSDKMethodNotFoundError(
                f"futopt_accounting on {type(self.client).__name__} has no close_position_record()."
            )

        account_id = _resolve_account_id(account) or "UNKNOWN"
        try:
            if end_date is not None:
                response = method(account, start_date, end_date)
            else:
                response = method(account, start_date)
        except TypeError:
            response = method(account, start_date, end_date)

        entries = _extract_list(response.get("data") if isinstance(response, Mapping) else response)
        records: List[ClosePositionRecord] = []
        for entry in entries:
            try:
                records.append(self._to_close_position_record(entry, account_id))
            except Exception as exc:
                self.logger.debug("Failed to map close position payload %s: %s", entry, exc)
        return records

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

        used_keys: set[str] = set()

        def pick(*keys: str) -> Any:
            used_keys.update(keys)
            return _first_value(payload, *keys)

        def pick_decimal(*keys: str) -> Decimal:
            return _ensure_decimal(pick(*keys))

        account_id = pick("account", "account_id", "acct", "user_id") or "UNKNOWN"
        currency = pick("currency", "Currency") or "TWD"
        balance = pick_decimal("equity", "balance", "cash", "total_asset", "today_equity", "todayEquity")
        raw_available = pick("available", "cash_available", "available_margin", "availableMargin")
        available = _ensure_decimal(raw_available) if raw_available is not None else balance
        frozen = pick_decimal("frozen", "hold", "on_hold", "margin_frozen")

        yesterday_balance = pick_decimal("yesterday_balance", "yesterdayBalance", "prev_balance", "previous_balance")
        today_balance = pick_decimal("today_balance", "todayBalance", "balance_today")
        raw_today_equity = pick("today_equity", "todayEquity", "equity_today")
        today_equity = _ensure_decimal(raw_today_equity) if raw_today_equity is not None else balance
        initial_margin = pick_decimal("initial_margin", "init_margin", "initialMargin")
        maintenance_margin = pick_decimal("maintenance_margin", "maintain_margin", "maintenanceMargin")
        clearing_margin = pick_decimal("clearing_margin", "clearingMargin", "settlement_margin")
        excess_margin = pick_decimal("excess_margin", "excessMargin")
        raw_available_margin = pick("available_margin", "availableMargin", "cash_available", "available")
        available_margin = _ensure_decimal(raw_available_margin) if raw_available_margin is not None else available
        disgorgement = pick_decimal("disgorgement", "margin_call", "callMargin")
        fut_realized_pnl = pick_decimal("fut_realized_pnl", "futures_realized_pnl", "realizedPnl")
        fut_unrealized_pnl = pick_decimal("fut_unrealized_pnl", "futures_unrealized_pnl", "unrealizedPnl")
        opt_value = pick_decimal("opt_value", "option_value", "optionValue")
        opt_long_value = pick_decimal("opt_long_value", "optLongValue")
        opt_short_value = pick_decimal("opt_short_value", "optShortValue")
        opt_pnl = pick_decimal("opt_pnl", "option_pnl", "optionPnl")
        today_fee = pick_decimal("today_fee", "fee", "total_fee", "todayFee")
        today_tax = pick_decimal("today_tax", "tax", "total_tax", "todayTax")
        today_cash_in = pick_decimal("today_cash_in", "cash_in", "todayDeposit", "deposit")
        today_cash_out = pick_decimal("today_cash_out", "cash_out", "todayWithdrawal", "withdraw")

        timestamp_raw = pick("timestamp", "update_time", "date", "as_of")
        timestamp = _parse_timestamp(timestamp_raw)

        used_keys.update({"account", "account_id", "acct", "user_id", "currency", "Currency"})

        extra = {key: value for key, value in payload.items() if key not in used_keys}

        account_data = AccountData(
            accountid=str(account_id),
            balance=balance,
            frozen=frozen,
            available=available if available else balance,
            currency=str(currency),
            gateway_name=self.gateway_name,
            timestamp=timestamp,
            yesterday_balance=yesterday_balance,
            today_balance=today_balance,
            today_equity=today_equity if today_equity else balance,
            initial_margin=initial_margin,
            maintenance_margin=maintenance_margin,
            clearing_margin=clearing_margin,
            excess_margin=excess_margin,
            available_margin=available_margin if available_margin else available,
            disgorgement=disgorgement,
            fut_realized_pnl=fut_realized_pnl,
            fut_unrealized_pnl=fut_unrealized_pnl,
            opt_value=opt_value,
            opt_long_value=opt_long_value,
            opt_short_value=opt_short_value,
            opt_pnl=opt_pnl,
            today_fee=today_fee,
            today_tax=today_tax,
            today_cash_in=today_cash_in,
            today_cash_out=today_cash_out,
            extra=extra,
        )

        self.logger.debug("Mapped account payload %s to %s", payload, account_data)
        return account_data

    def _to_equity_data(self, payload: Mapping[str, Any], account_id: str) -> EquityData:
        if not isinstance(payload, Mapping):
            raise TypeError(f"Equity payload must be mapping, got {type(payload)}")

        used_keys: set[str] = set()

        def pick(*keys: str) -> Any:
            used_keys.update(keys)
            return _first_value(payload, *keys)

        def pick_decimal(*keys: str) -> Decimal:
            return _ensure_decimal(pick(*keys))

        currency = pick("currency", "Currency") or "TWD"
        yesterday_balance = pick_decimal("yesterday_balance", "yesterdayBalance", "prev_balance", "previous_balance")
        today_balance = pick_decimal("today_balance", "todayBalance", "balance_today")
        today_equity = pick_decimal("today_equity", "todayEquity", "equity", "equity_today")
        initial_margin = pick_decimal("initial_margin", "init_margin", "initialMargin")
        maintenance_margin = pick_decimal("maintenance_margin", "maintain_margin", "maintenanceMargin")
        clearing_margin = pick_decimal("clearing_margin", "clearingMargin", "settlement_margin")
        excess_margin = pick_decimal("excess_margin", "excessMargin")
        available_margin = pick_decimal("available_margin", "availableMargin")
        disgorgement = pick_decimal("disgorgement", "margin_call", "callMargin")
        fut_realized_pnl = pick_decimal("fut_realized_pnl", "futures_realized_pnl", "realizedPnl")
        fut_unrealized_pnl = pick_decimal("fut_unrealized_pnl", "futures_unrealized_pnl", "unrealizedPnl")
        opt_value = pick_decimal("opt_value", "option_value", "optionValue")
        opt_long_value = pick_decimal("opt_long_value", "optLongValue")
        opt_short_value = pick_decimal("opt_short_value", "optShortValue")
        opt_pnl = pick_decimal("opt_pnl", "option_pnl", "optionPnl")
        today_fee = pick_decimal("today_fee", "fee", "total_fee", "todayFee")
        today_tax = pick_decimal("today_tax", "tax", "total_tax", "todayTax")
        today_cash_in = pick_decimal("today_cash_in", "cash_in", "todayDeposit", "deposit")
        today_cash_out = pick_decimal("today_cash_out", "cash_out", "todayWithdrawal", "withdraw")

        timestamp_raw = pick("date", "timestamp", "as_of", "update_time")
        timestamp = _parse_timestamp(timestamp_raw)

        used_keys.update({"account", "account_id", "acct", "accountId", "currency", "Currency"})
        extra = {key: value for key, value in payload.items() if key not in used_keys}

        return EquityData(
            accountid=str(account_id),
            currency=str(currency),
            today_equity=today_equity,
            yesterday_balance=yesterday_balance,
            today_balance=today_balance,
            initial_margin=initial_margin,
            maintenance_margin=maintenance_margin,
            clearing_margin=clearing_margin,
            excess_margin=excess_margin,
            available_margin=available_margin,
            disgorgement=disgorgement,
            fut_realized_pnl=fut_realized_pnl,
            fut_unrealized_pnl=fut_unrealized_pnl,
            opt_value=opt_value,
            opt_long_value=opt_long_value,
            opt_short_value=opt_short_value,
            opt_pnl=opt_pnl,
            today_fee=today_fee,
            today_tax=today_tax,
            today_cash_in=today_cash_in,
            today_cash_out=today_cash_out,
            timestamp=timestamp,
            extra=extra,
        )

    def _to_close_position_record(self, payload: Mapping[str, Any], account_id: str) -> ClosePositionRecord:
        if not isinstance(payload, Mapping):
            raise TypeError(f"Close position payload must be mapping, got {type(payload)}")

        used_keys: set[str] = set()

        def pick(*keys: str) -> Any:
            used_keys.update(keys)
            return _first_value(payload, *keys)

        def pick_decimal(*keys: str) -> Decimal:
            return _ensure_decimal(pick(*keys))

        symbol = str(pick("symbol", "code", "contractId") or "")
        exchange = _normalize_exchange(pick("exchange", "market"))
        direction = _normalise_direction(pick("direction", "side"))
        volume = pick_decimal("volume", "qty", "quantity")
        price = pick_decimal("price", "match_price", "closePrice")
        pnl = pick_decimal("pnl", "realized_pnl", "realizedPnl")
        close_time = _parse_timestamp(pick("close_time", "closeTime", "timestamp", "date"))

        used_keys.update({"symbol", "code", "contractId", "exchange", "market", "direction", "side"})
        extra = {key: value for key, value in payload.items() if key not in used_keys}

        record = ClosePositionRecord(
            accountid=str(account_id),
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            volume=volume,
            price=price,
            pnl=pnl,
            close_time=close_time,
            extra=extra,
        )
        return record

    def _to_position_data(self, payload: Mapping[str, Any]) -> PositionData:
        if not isinstance(payload, Mapping):
            raise TypeError(f"Position payload must be mapping, got {type(payload)}")

        used_keys: set[str] = set()

        def pick(*keys: str) -> Any:
            used_keys.update(keys)
            return _first_value(payload, *keys)

        def pick_decimal(*keys: str) -> Decimal:
            return _ensure_decimal(pick(*keys))

        symbol = str(pick("symbol", "code", "contractId") or "")
        exchange = _normalize_exchange(pick("exchange", "market"))
        direction = _normalise_direction(pick("direction", "side"))
        volume = pick_decimal("volume", "qty", "quantity", "net_volume")
        frozen = pick_decimal("frozen", "hold", "on_hold")
        price = pick_decimal("avg_price", "price", "average_price")
        pnl = pick_decimal("unrealized_pnl", "pnl", "unrealizedPnl")
        yd_volume = pick_decimal("yd_volume", "yesterday_volume", "overnight_position")

        timestamp_raw = pick("timestamp", "update_time", "as_of", "date")
        timestamp = _parse_timestamp(timestamp_raw)

        extra_fields: Dict[str, Any] = {
            "expiry_date": pick("expiry_date", "expiryDate"),
            "strike_price": pick("strike_price", "strikePrice"),
            "call_put": pick("call_put", "option_type", "callPut"),
            "is_spread": pick("is_spread", "isSpread"),
            "spreads": pick("spreads", "legs"),
            "margin": pick("margin", "required_margin"),
        }

        extra = {key: value for key, value in payload.items() if key not in used_keys}
        for key, value in extra_fields.items():
            if value not in (None, ""):
                extra.setdefault(key, value)

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
            extra=extra,
        )
        self.logger.debug("Mapped position payload %s to %s", payload, position)
        return position

