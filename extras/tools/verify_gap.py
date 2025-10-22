"""
資料品質檢核工具：對指定符號與時間區間檢查序號跳號、成交量對帳與 reconcile_log 狀態。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # pragma: no cover - 測試可用假 driver
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

try:  # pragma: no cover
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

try:  # pragma: no cover
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

LOGGER = logging.getLogger("vnpy_fubon.tools.verify_gap")
TAIWAN_TZ = ZoneInfo("Asia/Taipei") if ZoneInfo else timezone(timedelta(hours=8))


def load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_timestamp(text: str, *, local: bool = True) -> datetime:
    dt = datetime.fromisoformat(text)
    if local:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TAIWAN_TZ)
        return dt.astimezone(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def connect_pg(dsn: str):
    if psycopg is not None:  # pragma: no cover - 測試環境可注入
        return psycopg.connect(dsn)  # type: ignore[call-arg]
    if psycopg2 is not None:
        return psycopg2.connect(dsn)  # type: ignore[call-arg]
    raise RuntimeError("缺少 psycopg/psycopg2 driver，無法連線 PostgreSQL")


@dataclass
class GapFinding:
    symbol: str
    channel: str
    missing_ranges: List[Tuple[int, int]]
    count: int


def find_trade_gaps(cur, symbol: str, start: datetime, end: datetime) -> GapFinding:
    cur.execute(
        """
        SELECT event_seq
        FROM market_trades
        WHERE symbol = %s
          AND event_seq IS NOT NULL
          AND event_ts_utc BETWEEN %s AND %s
        ORDER BY event_seq
        """,
        (symbol, start, end),
    )
    rows = [seq for (seq,) in cur.fetchall()]
    gaps: List[Tuple[int, int]] = []
    for prev, nxt in zip(rows, rows[1:]):
        if nxt - prev > 1:
            gaps.append((prev, nxt))
    return GapFinding(symbol=symbol, channel="trades", missing_ranges=gaps, count=len(rows))


def find_l2_gaps(cur, symbol: str, start: datetime, end: datetime) -> GapFinding:
    cur.execute(
        """
        SELECT book_seq
        FROM market_l2
        WHERE symbol = %s
          AND level = 1
          AND book_seq IS NOT NULL
          AND event_ts_utc BETWEEN %s AND %s
        GROUP BY book_seq
        ORDER BY book_seq
        """,
        (symbol, start, end),
    )
    rows = [seq for (seq,) in cur.fetchall()]
    gaps: List[Tuple[int, int]] = []
    for prev, nxt in zip(rows, rows[1:]):
        if nxt - prev > 1:
            gaps.append((prev, nxt))
    return GapFinding(symbol=symbol, channel="orderbook", missing_ranges=gaps, count=len(rows))


def volume_mismatch(cur, symbol: str, start: datetime, end: datetime) -> Optional[float]:
    cur.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM market_trades
        WHERE symbol = %s
          AND event_ts_utc BETWEEN %s AND %s
        """,
        (symbol, start, end),
    )
    trade_volume = cur.fetchone()[0] or 0

    cur.execute(
        """
        SELECT
            COALESCE(MAX(volume), 0) - COALESCE(MIN(volume), 0) AS delta
        FROM market_quotes
        WHERE symbol = %s
          AND event_ts_utc BETWEEN %s AND %s
        """,
        (symbol, start, end),
    )
    quote_delta = cur.fetchone()[0] or 0

    if quote_delta == 0:
        return None
    return float(abs(trade_volume - quote_delta) / quote_delta)


def outstanding_reconcile(cur, symbol_pattern: str) -> List[Tuple[str, str, str]]:
    cur.execute(
        """
        SELECT symbol, channel, gap_status
        FROM reconcile_log
        WHERE symbol LIKE %s AND gap_status <> 'backfilled'
        ORDER BY gap_detected_at DESC
        """,
        (symbol_pattern,),
    )
    return cur.fetchall()


def collect_symbols(cur, symbol_pattern: str, start: datetime, end: datetime) -> List[str]:
    cur.execute(
        """
        SELECT DISTINCT symbol
        FROM market_raw
        WHERE symbol LIKE %s
          AND event_ts_utc BETWEEN %s AND %s
        ORDER BY symbol
        """,
        (symbol_pattern, start, end),
    )
    return [row[0] for row in cur.fetchall()]


def verify(
    dsn: str,
    symbol_pattern: str,
    start: datetime,
    end: datetime,
    *,
    tolerance: float,
) -> bool:
    conn = connect_pg(dsn)
    success = True
    try:
        with conn.cursor() as cur:
            symbols = collect_symbols(cur, symbol_pattern, start, end)
            if not symbols:
                LOGGER.warning("區間內未找到任何符號，請確認條件")
                return False

            LOGGER.info("檢核符號：%s", symbols)
            for symbol in symbols:
                trade_gaps = find_trade_gaps(cur, symbol, start, end)
                book_gaps = find_l2_gaps(cur, symbol, start, end)
                mismatch_ratio = volume_mismatch(cur, symbol, start, end)

                if trade_gaps.missing_ranges:
                    success = False
                    LOGGER.error("成交序號缺口 symbol=%s gaps=%s", symbol, trade_gaps.missing_ranges)
                if book_gaps.missing_ranges:
                    success = False
                    LOGGER.error("L2 序號缺口 symbol=%s gaps=%s", symbol, book_gaps.missing_ranges)

                if mismatch_ratio is not None and mismatch_ratio > tolerance:
                    success = False
                    LOGGER.error(
                        "成交量與 quotes 差異超標 symbol=%s ratio=%.4f tolerance=%.4f",
                        symbol,
                        mismatch_ratio,
                        tolerance,
                    )
                else:
                    LOGGER.info(
                        "成交量檢核 symbol=%s ratio=%s tolerance=%.4f",
                        symbol,
                        f"{mismatch_ratio:.4f}" if mismatch_ratio is not None else "N/A",
                        tolerance,
                    )

            pending = outstanding_reconcile(cur, symbol_pattern)
            if pending:
                success = False
                LOGGER.error("尚有未結 reconcile 記錄：%s", pending)

    finally:
        conn.close()
    return success


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="檢查資料缺口與成交量一致性")
    parser.add_argument("--symbol", required=True, help="符號樣式（可使用 LIKE 語法，例如 TXF%%）")
    parser.add_argument("--from-ts", required=True, help="起始時間（ISO，預設視為台北時間）")
    parser.add_argument("--to-ts", required=True, help="結束時間（ISO，預設視為台北時間）")
    parser.add_argument("--tolerance", type=float, default=0.01, help="成交量容忍百分比（預設 0.01）")
    parser.add_argument("--root", default=".", help="專案根目錄，預設當前路徑")
    parser.add_argument("--log-level", default="INFO", help="日誌層級")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    base_path = Path(args.root).resolve()
    env_path = base_path / ".env"
    env_values = load_env_file(env_path)
    os.environ.update({k: v for k, v in env_values.items() if k not in os.environ})

    dsn = os.environ.get("PG_DSN")
    if not dsn:
        raise SystemExit("缺少 PG_DSN，請於 .env 設定 PostgreSQL 連線字串")

    start = parse_timestamp(args.from_ts)
    end = parse_timestamp(args.to_ts)

    LOGGER.info("檢核範圍 %s ~ %s (UTC)", start, end)
    success = verify(dsn, args.symbol, start, end, tolerance=args.tolerance)

    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
