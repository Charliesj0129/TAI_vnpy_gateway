"""富邦行情訂閱與資料庫入庫腳本。

重新建構版本，負責：
- 載入 .env / TOML 設定。
- 將符號集合依當日時間展開為實際商品代碼。
- 建立富邦 WebSocket 連線並持續寫入 PostgreSQL。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import re
import threading
import signal
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
EXTRAS_ROOT = Path(__file__).resolve().parents[1]
if str(EXTRAS_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTRAS_ROOT))

from adapters import FubonToVnpyAdapter, NormalizedOrderBook, NormalizedQuote, NormalizedTrade, RawEnvelope
from clients import ClientState, FubonAPIClient, FubonCredentials, Subscription
from storage.pg_writer import PostgresWriter, RetryPolicy, WriterConfig
from vnpy_fubon.logging_config import configure_logging
from vnpy_fubon.vnpy_compat import (
    EVENT_FUBON_MARKET_RAW,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
)

try:  # pragma: no cover - Python 3.11 內建 tomllib
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

try:  # pragma: no cover
    import tomli as _tomli
except ModuleNotFoundError:  # pragma: no cover
    _tomli = None  # type: ignore[assignment]

LOGGER = logging.getLogger("vnpy_fubon.tools.subscribe")
TAIWAN_TZ = timezone(timedelta(hours=8))

DAY_SESSION_START = time(8, 45)
DAY_SESSION_END = time(13, 45)
NIGHT_SESSION_START = time(15, 0)
NIGHT_SESSION_END = time(5, 0)


def determine_session(now: datetime) -> str:
    local_time = now.astimezone(TAIWAN_TZ).time()
    if DAY_SESSION_START <= local_time < DAY_SESSION_END:
        return "day"
    if local_time >= NIGHT_SESSION_START or local_time < NIGHT_SESSION_END:
        return "night"
    return "idle"


def next_session_boundary(now: datetime) -> datetime:
    local_now = now.astimezone(TAIWAN_TZ)
    candidates: List[datetime] = []
    for day_offset in range(0, 2):
        base_date = (local_now + timedelta(days=day_offset)).date()
        for boundary in (time(5, 0), DAY_SESSION_START, DAY_SESSION_END, NIGHT_SESSION_START):
            candidate = datetime.combine(base_date, boundary, tzinfo=TAIWAN_TZ)
            if candidate > local_now:
                candidates.append(candidate)
    if not candidates:
        return local_now + timedelta(hours=1)
    return min(candidates)


# --------------------------------------------------------------------------- #
# 基礎工具


def load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    env: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def load_toml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    if tomllib is not None:
        with path.open("rb") as fh:
            return tomllib.load(fh)  # type: ignore[misc]
    if _tomli is not None:
        with path.open("rb") as fh:
            return _tomli.load(fh)
    LOGGER.warning("缺少 tomllib/tomli，無法解析 %s", path)
    return {}


def parse_channels(text: str) -> List[str]:
    return [chunk.strip().lower() for chunk in text.split(",") if chunk.strip()]


@dataclass
class SymbolRequest:
    base: str
    variant: str


def parse_symbol_tokens(tokens: Sequence[str]) -> List[SymbolRequest]:
    requests: List[SymbolRequest] = []
    for token in tokens:
        if not token:
            continue
        if "=" in token:
            base, variant = token.split("=", 1)
        elif "." in token:
            base, variant = token.split(".", 1)
        else:
            requests.append(SymbolRequest(base=token, variant="literal"))
            continue
        requests.append(SymbolRequest(base=base.strip().upper(), variant=variant.strip().lower()))
    return requests


def _coerce_symbol_set_members(value: Any) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        members: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                members.append(text)
        return members
    return []


def _build_symbol_set_lookup(symbols_cfg: Mapping[str, Any]) -> Dict[str, List[str]]:
    lookup: Dict[str, List[str]] = {}
    sets_cfg = symbols_cfg.get("symbol_sets", {})

    default_members = _coerce_symbol_set_members(sets_cfg.get("default"))
    if default_members:
        lookup["default"] = default_members

    custom_sets = sets_cfg.get("custom")
    if isinstance(custom_sets, Sequence) and not isinstance(custom_sets, (str, bytes, bytearray)):
        for entry in custom_sets:
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("name") or "").strip().lower()
            members = _coerce_symbol_set_members(entry.get("members"))
            if name and members:
                lookup[name] = members
    return lookup


def expand_symbol_inputs(tokens: Sequence[str], symbols_cfg: Mapping[str, Any]) -> List[str]:
    lookup = _build_symbol_set_lookup(symbols_cfg)
    expanded: List[str] = []

    for token in tokens:
        text = token.strip()
        if not text:
            continue
        if text.startswith("@"):
            set_name = text.lstrip("@").strip().lower()
            members = lookup.get(set_name)
            if not members:
                LOGGER.warning("符號集合 %s 不存在，請檢查 config/symbols.toml", text)
                continue
            expanded.extend(members)
            continue
        expanded.append(text)

    if not expanded:
        fallback = (
            lookup.get("rolling_aliases")
            or lookup.get("default")
            or ["TXF=front", "TXF=next"]
        )
        expanded.extend(fallback)
    return expanded


# --------------------------------------------------------------------------- #
# 符號展開邏輯（讀 config/symbols.toml）


class OptionTickerFetcher:
    def __init__(self, credentials: FubonCredentials, *, mode: str = "Normal") -> None:
        self._credentials = credentials
        self._mode = mode
        self._sdk: Optional[Any] = None
        self._rest_client: Optional[Any] = None
        self._lock = threading.Lock()

    def tickers(self, **params: Any) -> Mapping[str, Any]:
        client = self._ensure()
        return client.intraday.tickers(**params)

    def ticker(self, **params: Any) -> Mapping[str, Any]:
        client = self._ensure()
        return client.intraday.ticker(**params)

    def close(self) -> None:
        with self._lock:
            if self._sdk is not None:
                with contextlib.suppress(Exception):
                    self._sdk.logout()
            self._sdk = None
            self._rest_client = None

    def _ensure(self) -> Any:
        with self._lock:
            if self._rest_client is not None:
                return self._rest_client
            from fubon_neo.sdk import FubonSDK, Mode

            sdk = FubonSDK()
            sdk.login(
                self._credentials.user_id,
                self._credentials.user_password,
                self._credentials.ca_path,
                self._credentials.ca_password,
            )
            try:
                mode_enum = getattr(Mode, self._mode.capitalize())
            except AttributeError:
                mode_enum = Mode.Normal
            sdk.init_realtime(mode_enum)
            self._sdk = sdk
            self._rest_client = sdk.marketdata.rest_client.futopt
            return self._rest_client



class SymbolResolver:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        env: Mapping[str, str],
        rest_client: Optional[Any] = None,
        rest_enabled: bool = False,
    ) -> None:
        self.config = config
        self.env = env
        self._rest_client = rest_client
        self._rest_enabled = rest_enabled and rest_client is not None
        self._rest_cache: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
        self._strike_hint: Optional[float] = None

    @staticmethod
    def _cap_option_list(symbols: List[str], limit: int) -> List[str]:
        if limit <= 0 or len(symbols) <= limit:
            return symbols
        capped = symbols[:limit]
        if len(capped) % 2 and len(capped) > 1:
            capped = capped[:-1]
        return capped

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            text = str(value).strip().replace(",", "")
        except Exception:
            return None
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _align_to_spacing(value: float, spacing: int) -> int:
        if value <= 0:
            return 0
        if spacing <= 0:
            return int(round(value))
        return int(math.floor((value + spacing / 2) / spacing) * spacing)

    def _resolve_strike_center(self, cfg: Mapping[str, Any], now: datetime, spacing: int) -> int:
        base_value = self._strike_hint
        if base_value is None or base_value <= 0:
            base_value = self._compute_strike_hint(cfg, now)
            self._strike_hint = base_value
        if base_value is None or base_value <= 0:
            base_value = float(cfg.get("strike_center", 17000) or 17000)
        center = self._align_to_spacing(base_value, spacing)
        if center <= 0:
            center = spacing or int(base_value)
        return center

    def _compute_strike_hint(self, cfg: Mapping[str, Any], now: datetime) -> Optional[float]:
        env_value = self._to_float(self.env.get("TXO_STRIKE_CENTER"))
        if env_value:
            return env_value
        rest_value = self._fetch_underlying_price(now)
        if rest_value:
            return rest_value
        cfg_value = self._to_float(cfg.get("strike_center"))
        if cfg_value:
            return cfg_value
        return None

    def _fetch_underlying_price(self, now: datetime) -> Optional[float]:
        if not self._rest_enabled or self._rest_client is None:
            return None
        futures_cfg = self.config.get("futures", {})
        for name, meta in futures_cfg.items():
            if not isinstance(meta, Mapping):
                continue
            base_symbol = str(meta.get("base_symbol", name))
            roll_rule = meta.get("roll_rule", {})
            year, month = self._front_month(now, roll_rule)
            symbol = f"{base_symbol}{year % 100:02d}{month:02d}"
            for session in ("REGULAR", "AFTERHOURS", None):
                params: Dict[str, Any] = {"symbol": symbol}
                if session:
                    params["session"] = session
                try:
                    response = self._rest_client.intraday.ticker(**params)
                except Exception:  # pragma: no cover - REST errors are soft failures
                    LOGGER.debug("Failed to fetch ticker for %s", params, exc_info=True)
                    continue
                price = self._extract_price(response)
                if price:
                    return price
        return None

    def _extract_price(self, payload: Any) -> Optional[float]:
        if payload is None:
            return None
        if isinstance(payload, Mapping):
            for key in ("lastPrice", "last", "closePrice", "close", "price", "tradePrice", "referencePrice"):
                price = self._to_float(payload.get(key))
                if price:
                    return price
            for nested_key in ("data", "quote", "ticker", "result", "payload"):
                nested = payload.get(nested_key)
                price = self._extract_price(nested)
                if price:
                    return price
            return None
        if isinstance(payload, (list, tuple)):
            for item in payload:
                price = self._extract_price(item)
                if price:
                    return price
            return None
        for key in ("lastPrice", "last", "closePrice", "close", "price", "tradePrice"):
            if hasattr(payload, key):
                price = self._to_float(getattr(payload, key))
                if price:
                    return price
        if hasattr(payload, "__dict__"):
            return self._extract_price(vars(payload))
        return None

    def resolve(self, requests: Sequence[SymbolRequest]) -> List[str]:
        now = datetime.now(TAIWAN_TZ)
        resolved: List[str] = []
        futures_cfg = self.config.get("futures", {})
        options_cfg = self.config.get("options", {})

        for req in requests:
            if req.variant == "literal":
                resolved.append(req.base)
                continue

            if req.base in futures_cfg:
                resolved.extend(self._resolve_futures(req, futures_cfg[req.base], now))
                continue

            if req.base in options_cfg:
                resolved.extend(self._resolve_options(req, options_cfg[req.base], now))
                continue

            LOGGER.warning("無法辨識符號別名 %s，改用字串", req)
            resolved.append(req.base)

        seen: set[str] = set()
        unique: List[str] = []
        for item in resolved:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    # ----------------------- futures helper ----------------------- #

    def _resolve_futures(self, req: SymbolRequest, cfg: Mapping[str, Any], now: datetime) -> List[str]:
        variant_cfg = cfg.get(req.variant)
        if variant_cfg is None:
            LOGGER.warning("futures.%s 缺少設定 %s", req.base, req.variant)
            return []

        month_offset = int(variant_cfg.get("month_offset", 0))
        base_symbol = str(cfg.get("base_symbol", req.base))
        year, month = self._front_month(now, cfg.get("roll_rule", {}))
        year, month = _add_months(year, month, month_offset)
        alias_value = variant_cfg.get("alias")
        if isinstance(alias_value, str) and alias_value.strip():
            derived = alias_value.strip()
            if derived.startswith(base_symbol):
                return [derived]
        return [f"{base_symbol}{year % 100:02d}{month:02d}"]

    def _front_month(self, now: datetime, roll_rule: Mapping[str, Any]) -> Tuple[int, int]:
        method = roll_rule.get("method", "third_wednesday_minus_2d")
        switch_time = roll_rule.get("switch_time", "13:30")
        cutoff = _roll_cutoff(now.year, now.month, method, switch_time)
        base_year, base_month = now.year, now.month
        if now >= cutoff:
            base_year, base_month = _add_months(base_year, base_month, 1)
        return base_year, base_month

    # ----------------------- options helper ----------------------- #

    def _resolve_options(self, req: SymbolRequest, cfg: Mapping[str, Any], now: datetime) -> List[str]:
        if req.variant.startswith("weekly"):
            return self._resolve_weekly(cfg, now)
        if req.variant.startswith("monthly"):
            rest_symbols: List[str] = []
            if self._rest_enabled:
                rest_symbols = self._resolve_monthly_rest(req, cfg, now, req.variant)
            if rest_symbols:
                return rest_symbols
            return self._resolve_monthly(cfg, now, req.variant)
        LOGGER.warning("options.%s 不支援變體 %s", req.base, req.variant)
        return []

    def _resolve_weekly(self, cfg: Mapping[str, Any], now: datetime) -> List[str]:
        variant = cfg.get("weekly_all") or {}
        include_weeks = variant.get("include_weeks") or ["W1", "W2", "W3"]
        spacing = int(cfg.get("strike_spacing", 50))
        strikes_window = int(variant.get("strikes_window", cfg.get("strikes_window", 6)))
        max_contracts = int(variant.get("max_contracts", cfg.get("max_weekly_contracts", 0)))
        base_symbol = str(cfg.get("base_symbol", "TXO"))
        call_code = str(cfg.get("call_code", "C")).upper()
        put_code = str(cfg.get("put_code", "P")).upper()

        strike_center = self._resolve_strike_center(cfg, now, spacing)

        candidates: List[Tuple[Tuple[int, int, int], str]] = []
        for idx, week_code in enumerate(include_weeks):
            week = str(week_code).upper().strip()
            if not week:
                continue
            if week in {"NEXT", "NEXT_WEEK"}:
                offset = 1
                week = "W2"
            elif week in {"CUR", "CURR", "CURRENT", "THIS", "NEAR"}:
                offset = 0
                week = "W1"
            elif week.startswith("W") and week[1:].isdigit():
                offset = max(int(week[1:]) - 1, 0)
                week = f"W{offset + 1}"
            else:
                offset = idx
            week_date = now + timedelta(days=offset * 7)
            year = week_date.year
            month = week_date.month
            for delta in range(-strikes_window, strikes_window + 1):
                strike = strike_center + delta * spacing
                if strike <= 0:
                    continue
                priority = (offset, abs(delta), 0 if delta >= 0 else 1)
                call_symbol = f"{base_symbol}{year % 100:02d}{month:02d}{week}{call_code}{strike:05d}"
                put_symbol = f"{base_symbol}{year % 100:02d}{month:02d}{week}{put_code}{strike:05d}"
                candidates.append((priority, call_symbol))
                candidates.append((priority, put_symbol))

        candidates.sort(key=lambda item: item[0])
        ordered = [symbol for _, symbol in candidates]
        return self._cap_option_list(ordered, max_contracts)

    def _resolve_monthly(self, cfg: Mapping[str, Any], now: datetime, variant: str) -> List[str]:
        spacing = int(cfg.get("strike_spacing", 50))
        strikes_window = int(cfg.get("strikes_window", 8))
        max_contracts = int(cfg.get("max_monthly_contracts", 0))
        base_symbol = str(cfg.get("base_symbol", "TXO"))
        call_code = str(cfg.get("call_code", "C")).upper()
        put_code = str(cfg.get("put_code", "P")).upper()

        offset = 0
        if "next" in variant:
            offset = 1

        year, month = _add_months(now.year, now.month, offset)

        strike_center = self._resolve_strike_center(cfg, now, spacing)

        candidates: List[Tuple[Tuple[int, int, int], str]] = []
        for delta in range(-strikes_window, strikes_window + 1):
            strike = strike_center + delta * spacing
            if strike <= 0:
                continue
            priority = (0, abs(delta), 0 if delta >= 0 else 1)
            candidates.append((priority, f"{base_symbol}{year % 100:02d}{month:02d}{call_code}{strike:05d}"))
            candidates.append((priority, f"{base_symbol}{year % 100:02d}{month:02d}{put_code}{strike:05d}"))

        candidates.sort(key=lambda item: item[0])
        ordered = [symbol for _, symbol in candidates]
        return self._cap_option_list(ordered, max_contracts)

    def _resolve_monthly_rest(
        self,
        req: SymbolRequest,
        cfg: Mapping[str, Any],
        now: datetime,
        variant: str,
    ) -> List[str]:
        tickers = self._fetch_option_tickers(cfg, session="REGULAR")
        if not tickers:
            return []

        offset = 0
        if "next" in variant:
            offset = 1

        target_year, target_month = _add_months(now.year, now.month, offset)
        target_settlement = _third_wednesday(target_year, target_month).date().isoformat()

        spacing = int(cfg.get("strike_spacing", 50))
        strikes_window = int(cfg.get("strikes_window", 8))
        strike_center = self._resolve_strike_center(cfg, now, spacing)

        pattern = re.compile(r"^(TXO)(\d+)([A-Z])(\d)$")
        strikes: Dict[int, Dict[str, str]] = {}
        for item in tickers:
            if item.get("settlementDate") != target_settlement:
                continue
            symbol = str(item.get("symbol") or "")
            match = pattern.match(symbol)
            if not match:
                continue
            strike = int(match.group(2))
            side_code = match.group(3)
            if side_code in {"L", "C"}:
                side = "call"
            elif side_code in {"X", "P"}:
                side = "put"
            else:
                continue
            bucket = strikes.setdefault(strike, {})
            bucket[side] = symbol

        if not strikes:
            return []

        max_contracts = int(cfg.get("max_monthly_contracts", 0))
        desired: List[str] = []
        for delta in range(-strikes_window, strikes_window + 1):
            strike = strike_center + delta * spacing
            bucket = strikes.get(strike)
            if not bucket:
                continue
            if "call" in bucket:
                desired.append(bucket["call"])
            if "put" in bucket:
                desired.append(bucket["put"])

        if desired:
            return self._cap_option_list(desired, max_contracts)

        # Fallback to closest strikes when configured window not available
        sorted_strikes = sorted(strikes.keys(), key=lambda value: (abs(value - strike_center), value))
        for strike in sorted_strikes:
            bucket = strikes[strike]
            if "call" in bucket:
                desired.append(bucket["call"])
            if "put" in bucket:
                desired.append(bucket["put"])
            if len(desired) >= (strikes_window * 2 + 2):
                break
        return self._cap_option_list(desired, max_contracts)

    def _fetch_option_tickers(self, cfg: Mapping[str, Any], *, session: str) -> List[Mapping[str, Any]]:
        if not self._rest_enabled or self._rest_client is None:
            return []
        base_symbol = str(cfg.get("base_symbol", "TXO"))
        exchange = str(cfg.get("exchange", "TAIFEX"))
        cache_key = (base_symbol, session)
        if cache_key in self._rest_cache:
            return self._rest_cache[cache_key]
        try:
            response = self._rest_client.tickers(
                type="OPTION",
                exchange=exchange,
                session=session,
                symbol=base_symbol,
            )
            data = response.get("data", [])
        except Exception as exc:  # pragma: no cover - vendor behaviour
            LOGGER.exception("無法自 REST 取得 %s options tickers：%s", base_symbol, exc)
            data = []
        self._rest_cache[cache_key] = data
        return data


# --------------------------------------------------------------------------- #
# 簡易事件引擎（若沒有 vn.py event engine 時使用）


class SimpleEventEngine:
    def __init__(self) -> None:
        self._handlers: Dict[str, List] = defaultdict(list)

    def register(self, event_type: str, handler) -> None:
        self._handlers[event_type].append(handler)

    def put(self, event: Event) -> None:
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception:  # pragma: no cover - handler 例外不影響主流程
                LOGGER.exception("事件處理失敗 type=%s", event.type)


# --------------------------------------------------------------------------- #
# 主程序


async def main_async(args: argparse.Namespace) -> None:
    base_path = Path(args.root).resolve()
    env_path = base_path / ".env"
    env_values = load_env_file(env_path)
    os.environ.update({k: v for k, v in env_values.items() if k not in os.environ})

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    configure_logging(log_level=log_level, logger_name="vnpy_fubon.tools.subscribe")

    pg_dsn = os.environ.get("PG_DSN")
    if not pg_dsn:
        raise SystemExit("缺少 PG_DSN，請於 .env 設定 PostgreSQL 連線字串")

    credentials = FubonCredentials(
        user_id=os.environ.get("FUBON_USER_ID"),
        user_password=os.environ.get("FUBON_USER_PASSWORD"),
        ca_path=os.environ.get("FUBON_CA_PATH"),
        ca_password=os.environ.get("FUBON_CA_PASSWORD"),
    )

    missing_fields: List[str] = []
    if not credentials.user_id:
        missing_fields.append("FUBON_USER_ID")
    if not credentials.user_password:
        missing_fields.append("FUBON_USER_PASSWORD")
    if not credentials.ca_path:
        missing_fields.append("FUBON_CA_PATH")
    if not credentials.ca_password:
        missing_fields.append("FUBON_CA_PASSWORD")
    if missing_fields:
        raise SystemExit(f"缺少必要認證參數：{', '.join(missing_fields)}")

    pipeline_cfg = load_toml(base_path / "config" / "pipeline.toml")
    symbols_cfg = load_toml(base_path / "config" / "symbols.toml")

    batch_size = args.batch_size or int(
        os.environ.get(
            "BATCH_SIZE",
            pipeline_cfg.get("postgresql", {}).get("batch_size", 1000),
        )
    )

    retry_policy = RetryPolicy(
        max_attempts=int(os.environ.get("RETRY_MAX", pipeline_cfg.get("retry", {}).get("max_attempts", 5))),
        backoff_initial=float(pipeline_cfg.get("retry", {}).get("backoff_initial_ms", 500)) / 1000,
        backoff_multiplier=float(pipeline_cfg.get("retry", {}).get("backoff_multiplier", 2.0)),
        backoff_cap=float(pipeline_cfg.get("retry", {}).get("backoff_cap_ms", 15000)) / 1000,
    )

    writer = PostgresWriter(
        WriterConfig(dsn=pg_dsn, schema=os.environ.get("PG_SCHEMA", "public"), batch_size=batch_size),
        retry=retry_policy,
    )

    adapter = FubonToVnpyAdapter(gateway_name="FubonIngest")
    event_engine = SimpleEventEngine()

    raw_symbol_expr = args.symbols or os.environ.get("SYMBOL_SET")
    raw_tokens: List[str] = []
    if raw_symbol_expr:
        raw_tokens = [token.strip() for token in str(raw_symbol_expr).split(",") if token.strip()]
    tokens = expand_symbol_inputs(raw_tokens, symbols_cfg)

    fetch_option_flag = str(os.environ.get("FUBON_FETCH_OPTION_STRIKES", "0")).strip().lower() in {"1", "true", "yes"}
    ticker_fetcher: Optional[OptionTickerFetcher] = None
    if fetch_option_flag:
        ticker_fetcher = OptionTickerFetcher(
            credentials, mode=os.environ.get("FUBON_MARKET_MODE", "Normal") or "Normal"
        )

    resolver = SymbolResolver(
        symbols_cfg,
        env=os.environ,
        rest_client=ticker_fetcher,
        rest_enabled=fetch_option_flag,
    )
    symbol_list = resolver.resolve(parse_symbol_tokens(tokens))

    default_channels = ["trades", "orderbook"]
    channel_expr = args.channels or os.environ.get("CHANNELS")
    if channel_expr:
        requested = parse_channels(channel_expr)
        allowed = {"trades", "orderbook"}
        channels = [channel for channel in requested if channel in allowed]
        if not channels:
            LOGGER.warning("CHANNELS only supports trades/orderbook; falling back to default.")
            channels = default_channels
    else:
        channels = default_channels[:]

    depth = args.l2_depth or int(
        os.environ.get(
            "L2_DEPTH",
            pipeline_cfg.get("ingest", {}).get("l2_depth", 5),
        )
    )

    LOGGER.info("訂閱符號：%s", symbol_list)
    LOGGER.info("訂閱頻道：%s", channels)

    subscriptions: Dict[str, Subscription] = {
        symbol: Subscription(symbol=symbol, channels=channels, depth=depth) for symbol in symbol_list
    }
    stop_event = asyncio.Event()
    subscribed_symbols: set[str] = set()
    session_lock = asyncio.Lock()
    current_session: Optional[str] = None

    async def _persist(
        *,
        raw: Sequence[RawEnvelope] | None = None,
        orderbooks: Sequence[NormalizedOrderBook] | None = None,
        trades: Sequence[NormalizedTrade] | None = None,
        quotes: Sequence[NormalizedQuote] | None = None,
    ) -> None:
        await asyncio.to_thread(
            writer.ingest_bundle,
            raw=raw,
            orderbooks=orderbooks,
            trades=trades,
            quotes=quotes,
        )

    async def handle_message(payload: Mapping[str, Any]) -> None:
        channel = str(payload.get("channel") or payload.get("type") or "").lower()
        try:
            if "trade" in channel:
                trade = adapter.normalize_trade(payload)
                await _persist(raw=[trade.raw], trades=[trade])
                event_engine.put(Event(EVENT_FUBON_MARKET_RAW, trade.raw))
                event_engine.put(Event(EVENT_TRADE + trade.trade.symbol, trade.trade))
            elif any(tag in channel for tag in ("book", "depth", "orderbook")):
                book = adapter.normalize_orderbook(payload, depth=depth)
                await _persist(raw=[book.raw], orderbooks=[book])
                event_engine.put(Event(EVENT_FUBON_MARKET_RAW, book.raw))
                event_engine.put(Event(EVENT_TICK + book.tick.symbol, book.tick))
            elif "quote" in channel:
                quote = adapter.normalize_quote(payload)
                await _persist(raw=[quote.raw], quotes=[quote])
                event_engine.put(Event(EVENT_FUBON_MARKET_RAW, quote.raw))
                event_engine.put(Event(EVENT_TICK + quote.tick.symbol, quote.tick))
            else:
                raw = adapter.build_raw_envelope(payload, default_channel=channel or "misc")
                await _persist(raw=[raw])
                event_engine.put(Event(EVENT_FUBON_MARKET_RAW, raw))
        except Exception as exc:  # pragma: no cover - defensively log runtime errors
            LOGGER.exception("處理 WS 訊息失敗 payload=%s", payload, exc_info=exc)

    def on_state_change(state: ClientState) -> None:
        LOGGER.info("串流狀態切換：%s", state.name)

    market_mode = os.environ.get("FUBON_MARKET_MODE", "Normal")
    client = FubonAPIClient(
        credentials=credentials,
        mode=market_mode,
        on_message=handle_message,
        on_state_change=on_state_change,
    )

    # Session management helpers will handle subscribe/unsubscribe.
    async def apply_session(session: str) -> None:
        nonlocal current_session
        async with session_lock:
            if session == current_session:
                return
            if session == "idle":
                if subscribed_symbols:
                    LOGGER.info("Switching to idle window; cancelling all subscriptions.")
                    for symbol in list(subscribed_symbols):
                        await client.unsubscribe(symbol)
                    subscribed_symbols.clear()
                current_session = session
                return
            after_hours = session == "night"
            LOGGER.info("Switching to %s session (afterHours=%s)", "night" if after_hours else "day", after_hours)
            if subscribed_symbols:
                for symbol in list(subscribed_symbols):
                    await client.unsubscribe(symbol)
                subscribed_symbols.clear()
            for symbol, sub in subscriptions.items():
                sub.after_hours = after_hours
                await client.subscribe(sub)
                subscribed_symbols.add(symbol)
            current_session = session

    async def session_watcher() -> None:
        while not stop_event.is_set():
            now_local = datetime.now(TAIWAN_TZ)
            session = determine_session(now_local)
            await apply_session(session)
            boundary = next_session_boundary(now_local)
            timeout = max((boundary - now_local).total_seconds(), 60.0)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
                break
            except asyncio.TimeoutError:
                continue

    session_task: Optional[asyncio.Task[None]] = None

    await client.start()
    initial_session = determine_session(datetime.now(TAIWAN_TZ))
    await apply_session(initial_session)
    session_task = asyncio.create_task(session_watcher())

    def _request_stop(*_: Any) -> None:
        LOGGER.info("Received stop signal, shutting down.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    if hasattr(signal, "SIGINT"):
        try:
            loop.add_signal_handler(signal.SIGINT, _request_stop)
            loop.add_signal_handler(signal.SIGTERM, _request_stop)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        LOGGER.info("使用者中斷，準備關閉")
    finally:
        stop_event.set()
        if session_task:
            session_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session_task
        await client.stop()
        writer.close()
        if ticker_fetcher:
            ticker_fetcher.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="富邦 WS 訂閱入庫工具")
    parser.add_argument("--root", default=".", help="專案根目錄（預設為當前路徑）")
    parser.add_argument("--symbols", help="欲訂閱符號列表，逗號分隔，例如 TXF=front,TXF=next 或 @rolling_aliases")
    parser.add_argument("--channels", help="訂閱頻道清單（例 trades,orderbook）")
    parser.add_argument("--l2-depth", type=int, help="L2 深度（預設 5 層）")
    parser.add_argument("--batch-size", type=int, help="寫入批次大小（預設 1000）")
    parser.add_argument("--log-level", default="INFO", help="日誌層級")
    return parser


def _add_months(year: int, month: int, offset: int) -> Tuple[int, int]:
    total = year * 12 + (month - 1) + offset
    new_year = total // 12
    new_month = total % 12 + 1
    return new_year, new_month


def _third_wednesday(year: int, month: int) -> datetime:
    dt = datetime(year, month, 1, tzinfo=TAIWAN_TZ)
    count = 0
    while True:
        if dt.weekday() == 2:
            count += 1
            if count == 3:
                return dt
        dt += timedelta(days=1)


def _roll_cutoff(year: int, month: int, method: str, switch_time: str) -> datetime:
    base = _third_wednesday(year, month)
    if method == "third_wednesday_minus_2d":
        base -= timedelta(days=2)
    hour, minute = map(int, switch_time.split(":"))
    return datetime(
        base.year,
        base.month,
        base.day,
        hour=hour,
        minute=minute,
        tzinfo=TAIWAN_TZ,
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        asyncio.run(main_async(args))
    except Exception as exc:  # pragma: no cover - CLI 頂層日誌
        LOGGER.exception("fubon_subscribe 執行失敗", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


