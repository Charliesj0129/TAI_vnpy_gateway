"""
PostgreSQL 寫入工具：負責批次落地富邦行情資料並處理去重與重試。
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from adapters import BookRow, NormalizedOrderBook, NormalizedQuote, NormalizedTrade, RawEnvelope

try:  # pragma: no cover - 測試時可使用假連線
    import psycopg
    from psycopg import Connection  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    Connection = Any  # type: ignore[misc, assignment]

try:  # pragma: no cover
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

LOGGER = logging.getLogger("vnpy_fubon.storage.pg_writer")


@dataclass
class WriterConfig:
    """寫入參數。"""

    dsn: str
    schema: str = "public"
    batch_size: int = 1000
    use_copy: bool = True
    max_workers: int = 1


@dataclass
class RetryPolicy:
    """重試策略。"""

    max_attempts: int = 5
    backoff_initial: float = 0.5
    backoff_multiplier: float = 2.0
    backoff_cap: float = 15.0


ConnectionFactory = Callable[[], Any]


class PostgresWriter:
    """
    將標準化後的行情資料批次寫入 PostgreSQL。

    支援以下特色：
    - 依需求選擇 COPY 或 executemany。
    - 以 `ON CONFLICT DO NOTHING` 達成去重。
    - 斷線或失敗時依 RetryPolicy 自動重試。
    """

    def __init__(
        self,
        config: WriterConfig,
        *,
        retry: Optional[RetryPolicy] = None,
        connection_factory: Optional[ConnectionFactory] = None,
    ) -> None:
        self.config = config
        self.retry = retry or RetryPolicy()
        self._connection_factory = connection_factory
        self._conn: Any = None

    # ------------------------------------------------------------------ #
    # 連線與基礎工具

    def _connect(self) -> Any:
        if self._connection_factory:
            return self._connection_factory()
        if psycopg is not None:
            LOGGER.debug("使用 psycopg 建立連線")
            return psycopg.connect(self.config.dsn)  # type: ignore[call-arg]
        if psycopg2 is not None:
            LOGGER.debug("使用 psycopg2 建立連線")
            return psycopg2.connect(self.config.dsn)  # type: ignore[call-arg]
        raise RuntimeError("找不到 psycopg 或 psycopg2，請安裝 PostgreSQL driver")

    def _ensure_conn(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - 關閉失敗僅記錄
                LOGGER.debug("關閉連線時發生例外", exc_info=True)
            self._conn = None

    def _reconnect(self) -> None:
        self.close()
        self._conn = self._connect()

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        conn = self._ensure_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SET search_path TO {self.config.schema}")
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _execute_with_retry(self, func: Callable[[], None], *, description: str) -> None:
        attempt = 0
        delay = self.retry.backoff_initial
        while True:
            try:
                func()
                return
            except Exception as exc:
                attempt += 1
                LOGGER.warning("寫入失敗（%s）：%s", description, exc)
                if attempt >= self.retry.max_attempts:
                    LOGGER.error("已達最大重試次數，仍失敗：%s", description)
                    raise
                time.sleep(delay)
                delay = min(delay * self.retry.backoff_multiplier, self.retry.backoff_cap)
                self._reconnect()

    # ------------------------------------------------------------------ #
    # 對外介面

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        if not envelopes:
            return

        def job() -> None:
            with self._cursor() as cur:
                rows = [
                    (
                        env.channel,
                        env.symbol,
                        env.seq,
                        env.checksum,
                        env.event_ts_utc,
                        env.event_ts_local,
                        json.dumps(env.payload, ensure_ascii=False),
                        env.latency_ms,
                        env.dedup_token(),
                    )
                    for env in envelopes
                ]
                sql = """
                    INSERT INTO market_raw (
                        channel, symbol, event_seq, checksum,
                        event_ts_utc, event_ts_local, payload,
                        receive_latency_ms, dedup_token
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedup_token) DO NOTHING
                """
                cur.executemany(sql, rows)

        self._execute_with_retry(job, description="market_raw 批次寫入")

    def write_orderbooks(self, books: Sequence[NormalizedOrderBook]) -> None:
        if not books:
            return

        def job() -> None:
            with self._cursor() as cur:
                rows: List[Tuple[Any, ...]] = []
                for book in books:
                    for row in book.rows:
                        rows.append(
                            (
                                row.symbol,
                                row.event_ts_utc,
                                row.event_ts_local,
                                row.level,
                                row.bid_px,
                                row.bid_sz,
                                row.ask_px,
                                row.ask_sz,
                                row.mid_px,
                                row.book_seq,
                                row.is_snapshot,
                                row.channel,
                                row.checksum,
                            )
                        )
                if not rows:
                    return
                sql = """
                    INSERT INTO market_l2 (
                        symbol, event_ts_utc, event_ts_local, level,
                        bid_px, bid_sz, ask_px, ask_sz, mid_px,
                        book_seq, is_snapshot, channel, checksum
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, event_ts_utc, level, is_snapshot) DO NOTHING
                """
                cur.executemany(sql, rows)

        self._execute_with_retry(job, description="market_l2 批次寫入")

    def write_trades(self, trades: Sequence[NormalizedTrade]) -> None:
        if not trades:
            return

        def job() -> None:
            with self._cursor() as cur:
                rows = [
                    (
                        item.row["symbol"],
                        item.row["trade_id"],
                        item.row["event_seq"],
                        item.row["side"],
                        item.row["price"],
                        item.row["quantity"],
                        item.row["turnover"],
                        item.row["event_ts_utc"],
                        item.row["event_ts_local"],
                        item.row["channel"],
                        item.row["checksum"],
                        None,
                    )
                    for item in trades
                ]
                sql = """
                    INSERT INTO market_trades (
                        symbol, trade_id, event_seq, side, price, quantity,
                        turnover, event_ts_utc, event_ts_local, channel,
                        checksum, source_payload_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, trade_id) DO NOTHING
                """
                cur.executemany(sql, rows)

        self._execute_with_retry(job, description="market_trades 批次寫入")

    def write_quotes(self, quotes: Sequence[NormalizedQuote]) -> None:
        if not quotes:
            return

        def job() -> None:
            with self._cursor() as cur:
                rows = [
                    (
                        item.row["symbol"],
                        item.row["event_ts_utc"],
                        item.row["event_ts_local"],
                        item.row["last_px"],
                        item.row["prev_close_px"],
                        item.row["open_px"],
                        item.row["high_px"],
                        item.row["low_px"],
                        item.row["bid_px_1"],
                        item.row["bid_sz_1"],
                        item.row["ask_px_1"],
                        item.row["ask_sz_1"],
                        item.row["volume"],
                        item.row["turnover"],
                        item.row["open_interest"],
                        item.row["implied_vol"],
                        item.row["est_settlement"],
                        item.row["book_seq"],
                        item.row["checksum"],
                        item.row["channel"],
                    )
                    for item in quotes
                ]
                sql = """
                    INSERT INTO market_quotes (
                        symbol, event_ts_utc, event_ts_local,
                        last_px, prev_close_px, open_px, high_px, low_px,
                        bid_px_1, bid_sz_1, ask_px_1, ask_sz_1,
                        volume, turnover, open_interest, implied_vol,
                        est_settlement, book_seq, checksum, channel
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, event_ts_utc) DO NOTHING
                """
                cur.executemany(sql, rows)

        self._execute_with_retry(job, description="market_quotes 批次寫入")

    def ingest_bundle(
        self,
        *,
        raw: Sequence[RawEnvelope] | None = None,
        orderbooks: Sequence[NormalizedOrderBook] | None = None,
        trades: Sequence[NormalizedTrade] | None = None,
        quotes: Sequence[NormalizedQuote] | None = None,
    ) -> None:
        """一次寫入多個資料面向，確保在同一交易中提交。"""

        raw = raw or []
        orderbooks = orderbooks or []
        trades = trades or []
        quotes = quotes or []

        def job() -> None:
            with self._cursor() as cur:
                if raw:
                    raw_rows = [
                        (
                            env.channel,
                            env.symbol,
                            env.seq,
                            env.checksum,
                            env.event_ts_utc,
                            env.event_ts_local,
                            json.dumps(env.payload, ensure_ascii=False),
                            env.latency_ms,
                            env.dedup_token(),
                        )
                        for env in raw
                    ]
                    cur.executemany(
                        """
                        INSERT INTO market_raw (
                            channel, symbol, event_seq, checksum,
                            event_ts_utc, event_ts_local, payload,
                            receive_latency_ms, dedup_token
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (dedup_token) DO NOTHING
                        """,
                        raw_rows,
                    )

                if orderbooks:
                    l2_rows: List[Tuple[Any, ...]] = []
                    for book in orderbooks:
                        for row in book.rows:
                            l2_rows.append(
                                (
                                    row.symbol,
                                    row.event_ts_utc,
                                    row.event_ts_local,
                                    row.level,
                                    row.bid_px,
                                    row.bid_sz,
                                    row.ask_px,
                                    row.ask_sz,
                                    row.mid_px,
                                    row.book_seq,
                                    row.is_snapshot,
                                    row.channel,
                                    row.checksum,
                                )
                            )
                    if l2_rows:
                        cur.executemany(
                            """
                            INSERT INTO market_l2 (
                                symbol, event_ts_utc, event_ts_local, level,
                                bid_px, bid_sz, ask_px, ask_sz, mid_px,
                                book_seq, is_snapshot, channel, checksum
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (symbol, event_ts_utc, level, is_snapshot) DO NOTHING
                            """,
                            l2_rows,
                        )

                if trades:
                    trade_rows = [
                        (
                            item.row["symbol"],
                            item.row["trade_id"],
                            item.row["event_seq"],
                            item.row["side"],
                            item.row["price"],
                            item.row["quantity"],
                            item.row["turnover"],
                            item.row["event_ts_utc"],
                            item.row["event_ts_local"],
                            item.row["channel"],
                            item.row["checksum"],
                            None,
                        )
                        for item in trades
                    ]
                    cur.executemany(
                        """
                        INSERT INTO market_trades (
                            symbol, trade_id, event_seq, side, price, quantity,
                            turnover, event_ts_utc, event_ts_local, channel,
                            checksum, source_payload_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, trade_id) DO NOTHING
                        """,
                        trade_rows,
                    )

                if quotes:
                    quote_rows = [
                        (
                            item.row["symbol"],
                            item.row["event_ts_utc"],
                            item.row["event_ts_local"],
                            item.row["last_px"],
                            item.row["prev_close_px"],
                            item.row["open_px"],
                            item.row["high_px"],
                            item.row["low_px"],
                            item.row["bid_px_1"],
                            item.row["bid_sz_1"],
                            item.row["ask_px_1"],
                            item.row["ask_sz_1"],
                            item.row["volume"],
                            item.row["turnover"],
                            item.row["open_interest"],
                            item.row["implied_vol"],
                            item.row["est_settlement"],
                            item.row["book_seq"],
                            item.row["checksum"],
                            item.row["channel"],
                        )
                        for item in quotes
                    ]
                    cur.executemany(
                        """
                        INSERT INTO market_quotes (
                            symbol, event_ts_utc, event_ts_local,
                            last_px, prev_close_px, open_px, high_px, low_px,
                            bid_px_1, bid_sz_1, ask_px_1, ask_sz_1,
                            volume, turnover, open_interest, implied_vol,
                            est_settlement, book_seq, checksum, channel
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, event_ts_utc) DO NOTHING
                        """,
                        quote_rows,
                    )

        self._execute_with_retry(job, description="整批寫入")


__all__ = ["PostgresWriter", "RetryPolicy", "WriterConfig"]
