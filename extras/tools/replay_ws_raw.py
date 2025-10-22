"""
從 market_raw 回放 WebSocket 封包，重建 L2 並比對既有資料，可選擇覆寫補洞。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters import FubonToVnpyAdapter, NormalizedOrderBook, RawEnvelope
from storage.pg_writer import PostgresWriter, WriterConfig

try:  # pragma: no cover
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

LOGGER = logging.getLogger("vnpy_fubon.tools.replay_raw")
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


def parse_timestamp(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIWAN_TZ)
    return dt.astimezone(timezone.utc)


def connect_pg(dsn: str):
    if psycopg is not None:  # pragma: no cover - 測試可注入
        return psycopg.connect(dsn)  # type: ignore[call-arg]
    if psycopg2 is not None:
        return psycopg2.connect(dsn)  # type: ignore[call-arg]
    raise RuntimeError("缺少 psycopg/psycopg2 driver，無法連線 PostgreSQL")


@dataclass
class ReplayResult:
    orderbooks: List[NormalizedOrderBook]
    raw_events: List[RawEnvelope]
    mismatches: List[str]


def fetch_market_raw(cur, symbol: str, start: datetime, end: datetime, channel: str) -> List[Mapping[str, Any]]:
    cur.execute(
        """
        SELECT id, channel, payload, event_ts_utc, symbol
        FROM market_raw
        WHERE symbol LIKE %s
          AND channel = %s
          AND event_ts_utc BETWEEN %s AND %s
        ORDER BY event_ts_utc, id
        """,
        (symbol, channel, start, end),
    )
    rows = []
    for row in cur.fetchall():
        payload = row[2]
        if isinstance(payload, str):
            payload = json.loads(payload)
        rows.append(
            {
                "id": row[0],
                "channel": row[1],
                "payload": payload,
                "event_ts_utc": row[3],
                "symbol": row[4],
            }
        )
    return rows


def compare_orderbook(cur, book: NormalizedOrderBook, *, tolerance: float) -> Optional[str]:
    cur.execute(
        """
        SELECT level, bid_px, bid_sz, ask_px, ask_sz
        FROM market_l2
        WHERE symbol = %s
          AND event_ts_utc = %s
          AND is_snapshot = %s
        ORDER BY level
        """,
        (
            book.raw.symbol,
            book.raw.event_ts_utc,
            book.rows[0].is_snapshot if book.rows else True,
        ),
    )
    db_rows = cur.fetchall()
    if not db_rows:
        return f"{book.raw.symbol} @ {book.raw.event_ts_utc} 缺少對應 L2 紀錄"

    for level, row in enumerate(book.rows, start=1):
        if level > len(db_rows):
            return f"{book.raw.symbol} @ {book.raw.event_ts_utc} level {level} 少於回放層數"
        db_level, db_bid_px, db_bid_sz, db_ask_px, db_ask_sz = db_rows[level - 1]
        if db_level != level:
            return f"{book.raw.symbol} @ {book.raw.event_ts_utc} level mismatch {db_level} != {level}"
        if not _close(db_bid_px, row.bid_px, tolerance) or not _close(db_bid_sz, row.bid_sz, tolerance):
            return f"{book.raw.symbol} @ {book.raw.event_ts_utc} bid level {level} 差異 db=({db_bid_px},{db_bid_sz}) replay=({row.bid_px},{row.bid_sz})"
        if not _close(db_ask_px, row.ask_px, tolerance) or not _close(db_ask_sz, row.ask_sz, tolerance):
            return f"{book.raw.symbol} @ {book.raw.event_ts_utc} ask level {level} 差異 db=({db_ask_px},{db_ask_sz}) replay=({row.ask_px},{row.ask_sz})"
    return None


def _close(a: Optional[float], b: Optional[float], tolerance: float) -> bool:
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    if a == 0:
        return abs(b) < tolerance
    return abs((float(a) - float(b)) / float(a)) <= tolerance


def replay(
    dsn: str,
    symbol_pattern: str,
    start: datetime,
    end: datetime,
    *,
    depth: int,
    tolerance: float,
    apply_changes: bool,
    batch_size: int,
) -> ReplayResult:
    conn = connect_pg(dsn)
    adapter = FubonToVnpyAdapter(gateway_name="FubonReplay")
    writer: Optional[PostgresWriter] = None
    if apply_changes:
        writer = PostgresWriter(WriterConfig(dsn=dsn, schema=os.environ.get("PG_SCHEMA", "public"), batch_size=batch_size))

    orderbooks: List[NormalizedOrderBook] = []
    raw_envelopes: List[RawEnvelope] = []
    mismatches: List[str] = []

    try:
        with conn.cursor() as cur:
            raw_rows = fetch_market_raw(cur, symbol_pattern, start, end, channel="orderbook")
            LOGGER.info("取得 orderbook 原始封包 %d 筆", len(raw_rows))
            for raw in raw_rows:
                payload = dict(raw["payload"])
                payload.setdefault("channel", raw["channel"])
                book = adapter.normalize_orderbook(payload, depth=depth)
                orderbooks.append(book)
                raw_envelopes.append(book.raw)

                mismatch = compare_orderbook(cur, book, tolerance=tolerance)
                if mismatch:
                    mismatches.append(mismatch)

            LOGGER.info("回放比對完成，差異筆數：%d", len(mismatches))

            if apply_changes and orderbooks:
                assert writer is not None
                for i in range(0, len(orderbooks), batch_size):
                    batch = orderbooks[i : i + batch_size]
                    writer.write_orderbooks(batch)
                LOGGER.info("已重新寫入 %d 筆 orderbook 記錄", len(orderbooks))
    finally:
        conn.close()
        if writer:
            writer.close()

    return ReplayResult(orderbooks=orderbooks, raw_events=raw_envelopes, mismatches=mismatches)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回放 market_raw 並重建 L2")
    parser.add_argument("--symbol", required=True, help="符號樣式（LIKE 模式，例如 TXF%%）")
    parser.add_argument("--from-ts", required=True, help="起始時間（ISO，預設視為台北時間）")
    parser.add_argument("--to-ts", required=True, help="結束時間（ISO，預設視為台北時間）")
    parser.add_argument("--depth", type=int, default=5, help="回放層數（預設 5）")
    parser.add_argument("--tolerance", type=float, default=0.0001, help="價量比對容忍誤差（百分比）")
    parser.add_argument("--apply", action="store_true", help="將回放結果重新寫回資料庫")
    parser.add_argument("--batch-size", type=int, default=500, help="寫回批次大小")
    parser.add_argument("--root", default=".", help="專案根目錄（預設當前路徑）")
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
        raise SystemExit("缺少 PG_DSN，請在 .env 設定")

    start = parse_timestamp(args.from_ts)
    end = parse_timestamp(args.to_ts)

    LOGGER.info("回放範圍 %s ~ %s (UTC)", start, end)
    result = replay(
        dsn,
        args.symbol,
        start,
        end,
        depth=args.depth,
        tolerance=args.tolerance,
        apply_changes=args.apply,
        batch_size=args.batch_size,
    )

    if result.mismatches:
        for item in result.mismatches[:20]:
            LOGGER.error("比對差異：%s", item)
        LOGGER.error("總共有 %d 筆差異", len(result.mismatches))
        if not args.apply:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
