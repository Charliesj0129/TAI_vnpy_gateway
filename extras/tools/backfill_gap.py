"""
依據 reconcile_log 或手動指定範圍補寫 L2 / trades / quotes。
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters import FubonToVnpyAdapter, NormalizedOrderBook, NormalizedQuote, NormalizedTrade
from storage.pg_writer import PostgresWriter, RetryPolicy, WriterConfig
from tools.replay_ws_raw import replay as replay_orderbook  # type: ignore[import]

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

LOGGER = logging.getLogger("vnpy_fubon.tools.backfill")
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


def connect_pg(dsn: str):
    if psycopg is not None:  # pragma: no cover
        return psycopg.connect(dsn)  # type: ignore[call-arg]
    if psycopg2 is not None:
        return psycopg2.connect(dsn)  # type: ignore[call-arg]
    raise RuntimeError("缺少 psycopg/psycopg2 driver，無法連線 PostgreSQL")


def parse_ts(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIWAN_TZ)
    return dt.astimezone(timezone.utc)


def fetch_reconcile(cur, reconcile_id: int) -> Mapping[str, Any]:
    cur.execute(
        """
        SELECT id, symbol, channel, start_seq, end_seq, start_ts_utc, end_ts_utc, retry_count
        FROM reconcile_log
        WHERE id = %s
        """,
        (reconcile_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"找不到 reconcile_log id={reconcile_id}")
    return {
        "id": row[0],
        "symbol": row[1],
        "channel": row[2],
        "start_seq": row[3],
        "end_seq": row[4],
        "start_ts_utc": row[5],
        "end_ts_utc": row[6],
        "retry_count": row[7],
    }


def fetch_raw_rows(cur, symbol: str, start: datetime, end: datetime, channel: str) -> List[Mapping[str, Any]]:
    cur.execute(
        """
        SELECT payload
        FROM market_raw
        WHERE symbol LIKE %s
          AND channel = %s
          AND event_ts_utc BETWEEN %s AND %s
        ORDER BY event_ts_utc
        """,
        (symbol, channel, start, end),
    )
    rows: List[Mapping[str, Any]] = []
    for (payload,) in cur.fetchall():
        if isinstance(payload, str):
            payload = json.loads(payload)
        rows.append(payload)
    return rows


def backfill_trades(conn, dsn: str, symbol: str, start: datetime, end: datetime, writer: PostgresWriter) -> int:
    adapter = FubonToVnpyAdapter(gateway_name="FubonBackfill")
    with conn.cursor() as cur:
        payloads = fetch_raw_rows(cur, symbol, start, end, channel="trades")
    if not payloads:
        LOGGER.warning("未從 market_raw 找到 trades payload")
        return 0

    trades: List[NormalizedTrade] = []
    for payload in payloads:
        payload.setdefault("channel", "trades")
        trades.append(adapter.normalize_trade(payload))

    writer.write_trades(trades)
    return len(trades)


def backfill_quotes(conn, dsn: str, symbol: str, start: datetime, end: datetime, writer: PostgresWriter) -> int:
    adapter = FubonToVnpyAdapter(gateway_name="FubonBackfill")
    with conn.cursor() as cur:
        payloads = fetch_raw_rows(cur, symbol, start, end, channel="quotes")
    if not payloads:
        LOGGER.warning("未從 market_raw 找到 quotes payload")
        return 0

    quotes = []
    for payload in payloads:
        payload.setdefault("channel", "quotes")
        quotes.append(adapter.normalize_quote(payload))

    writer.write_quotes(quotes)
    return len(quotes)


def update_reconcile_status(conn, reconcile_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE reconcile_log
            SET gap_status = %s,
                last_retry_at = NOW(),
                retry_count = retry_count + 1
            WHERE id = %s
            """,
            (status, reconcile_id),
        )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="根據 reconcile_log 或手動範圍回補資料")
    parser.add_argument("--reconcile-id", type=int, help="指定 reconcile_log.id，忽略其他參數")
    parser.add_argument("--symbol", help="符號樣式（LIKE），若不使用 reconcile-id 則必填")
    parser.add_argument("--channel", choices=["orderbook", "trades", "quotes"], help="要補洞的頻道")
    parser.add_argument("--from-ts", help="起始時間（ISO）")
    parser.add_argument("--to-ts", help="結束時間（ISO）")
    parser.add_argument("--depth", type=int, default=5, help="orderbook 回放層數")
    parser.add_argument("--batch-size", type=int, default=500, help="寫入批大小")
    parser.add_argument("--root", default=".", help="專案根目錄")
    parser.add_argument("--log-level", default="INFO", help="日誌層級")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    base_path = Path(args.root).resolve()
    env_path = base_path / ".env"
    env_values = load_env_file(env_path)
    os.environ.update({k: v for k, v in env_values.items() if k not in os.environ})

    dsn = os.environ.get("PG_DSN")
    if not dsn:
        raise SystemExit("缺少 PG_DSN，請在 .env 設定")

    conn = connect_pg(dsn)
    writer = PostgresWriter(
        WriterConfig(dsn=dsn, schema=os.environ.get("PG_SCHEMA", "public"), batch_size=args.batch_size),
        retry=RetryPolicy(),
    )

    try:
        if args.reconcile_id:
            with conn.cursor() as cur:
                reconcile = fetch_reconcile(cur, args.reconcile_id)
            symbol = reconcile["symbol"]
            channel = reconcile["channel"]
            start_ts = reconcile["start_ts_utc"] or (datetime.now(timezone.utc) - timedelta(minutes=5))
            end_ts = reconcile["end_ts_utc"] or datetime.now(timezone.utc)
            LOGGER.info("從 reconcile_log 補寫 id=%s symbol=%s channel=%s", args.reconcile_id, symbol, channel)
        else:
            if not (args.symbol and args.channel and args.from_ts and args.to_ts):
                raise SystemExit("請提供 --symbol, --channel, --from-ts, --to-ts 或使用 --reconcile-id")
            symbol = args.symbol
            channel = args.channel
            start_ts = parse_ts(args.from_ts)
            end_ts = parse_ts(args.to_ts)

        if channel == "orderbook":
            result = replay_orderbook(
                dsn,
                symbol,
                start_ts,
                end_ts,
                depth=args.depth,
                tolerance=0.0001,
                apply_changes=True,
                batch_size=args.batch_size,
            )
            LOGGER.info("orderbook 回補完成，筆數=%d", len(result.orderbooks))
        elif channel == "trades":
            count = backfill_trades(conn, dsn, symbol, start_ts, end_ts, writer)
            LOGGER.info("trades 回補完成，筆數=%d", count)
        elif channel == "quotes":
            count = backfill_quotes(conn, dsn, symbol, start_ts, end_ts, writer)
            LOGGER.info("quotes 回補完成，筆數=%d", count)
        else:
            raise SystemExit(f"未知頻道：{channel}")

        if args.reconcile_id:
            update_reconcile_status(conn, args.reconcile_id, "backfilled")
    except Exception as exc:
        LOGGER.exception("回補失敗：%s", exc)
        if args.reconcile_id:
            update_reconcile_status(conn, args.reconcile_id, "pending")
        raise SystemExit(1) from exc
    finally:
        conn.close()
        writer.close()


if __name__ == "__main__":
    main()
