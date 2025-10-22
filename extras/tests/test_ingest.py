import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from adapters import FubonToVnpyAdapter
from storage.pg_writer import PostgresWriter, WriterConfig


class FakeCursor:
    def __init__(self) -> None:
        self.commands = []

    def execute(self, sql, params=None) -> None:  # pragma: no cover - 直接記錄 SQL
        self.commands.append(("execute", sql.strip(), params))

    def executemany(self, sql, seq_params) -> None:
        self.commands.append(("executemany", sql.strip(), list(seq_params)))

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


def _build_adapter_payloads():
    orderbook_payload = {
        "channel": "books",
        "event": "data",
        "data": {
            "symbol": "TXF202510",
            "exchange": "TAIFEX",
            "time": "2025-10-16T01:00:00+00:00",
            "bids": [{"price": "16500", "size": "10"}],
            "asks": [{"price": "16501", "size": "8"}],
        },
    }
    trade_payload = {
        "channel": "trades",
        "event": "data",
        "data": {
            "symbol": "TXF202510",
            "exchange": "TAIFEX",
            "time": "2025-10-16T01:00:00+00:00",
            "serial": 1001,
            "trades": [
                {
                    "price": "16500",
                    "size": "5",
                    "side": "BUY",
                    "ask": "16501",
                    "bid": "16500",
                }
            ],
        },
    }
    quote_payload = {
        "channel": "quotes",
        "event": "data",
        "data": {
            "symbol": "TXF202510",
            "exchange": "TAIFEX",
            "time": "2025-10-16T01:00:00+00:00",
            "lastPrice": "16500",
            "openPrice": "16450",
            "highPrice": "16580",
            "lowPrice": "16400",
            "volume": "100",
            "bidPx1": "16500",
            "bidSz1": "30",
            "askPx1": "16502",
            "askSz1": "28",
            "openInterest": "15000",
        },
    }
    return orderbook_payload, trade_payload, quote_payload


def test_ingest_bundle_generates_expected_sql():
    adapter = FubonToVnpyAdapter()
    orderbook_payload, trade_payload, quote_payload = _build_adapter_payloads()

    orderbook = adapter.normalize_orderbook(orderbook_payload, depth=1)
    trade = adapter.normalize_trade(trade_payload)
    quote = adapter.normalize_quote(quote_payload)

    fake_conn = FakeConnection()
    writer = PostgresWriter(
        WriterConfig(dsn="postgresql://test", schema="public"),
        connection_factory=lambda: fake_conn,
    )

    writer.ingest_bundle(
        raw=[orderbook.raw, trade.raw, quote.raw],
        orderbooks=[orderbook],
        trades=[trade],
        quotes=[quote],
    )

    # 確認有執行 INSERT 指令
    executemany_calls = [cmd for cmd in fake_conn.cursors[0].commands if cmd[0] == "executemany"]
    assert any("INSERT INTO market_raw" in sql for _, sql, _ in executemany_calls)
    assert any("INSERT INTO market_l2" in sql for _, sql, _ in executemany_calls)
    assert any("INSERT INTO market_trades" in sql for _, sql, _ in executemany_calls)
    assert any("INSERT INTO market_quotes" in sql for _, sql, _ in executemany_calls)

    # 驗證 L2 資料內容
    l2_rows = next(rows for kind, sql, rows in executemany_calls if "market_l2" in sql)
    assert l2_rows[0][0] == "TXF202510"
    assert l2_rows[0][3] == 1
    assert l2_rows[0][4] == Decimal("16500")
    assert l2_rows[0][5] == Decimal("10")
    assert l2_rows[0][6] == Decimal("16501")
    assert l2_rows[0][7] == Decimal("8")

    # 驗證成交資料
    trade_rows = next(rows for kind, sql, rows in executemany_calls if "market_trades" in sql)
    row = trade_rows[0]
    assert row[0] == "TXF202510"
    assert row[1] == "1001"
    assert row[4] == Decimal("16500")
    assert row[5] == Decimal("5")

    # 驗證 quotes 資料
    quote_rows = next(rows for kind, sql, rows in executemany_calls if "market_quotes" in sql)
    row = quote_rows[0]
    assert row[0] == "TXF202510"
    assert row[3] == Decimal("16500")
    assert row[9] == Decimal("30")
