--
-- 富邦 L2 與成交資料儲存結構（PostgreSQL）
--
-- 時間欄位約定：
--   * event_ts_utc   : 資料源事件時間（UTC+0）
--   * event_ts_local : 事件時間換算亞洲/台北時區（UTC+8）
--   * ingest_ts_utc  : 寫入資料庫的時間戳（UTC+0）
-- 需啟用 `timezone = 'UTC'` 並在應用層轉換 Asia/Taipei。
--
-- 注意：所有表皆預期搭配 `SET search_path TO public;`，若需專屬 schema 請於部署階段調整。
--
-- 依據需求新增 pgcrypto 以支援雜湊（若尚未啟用）：
--   CREATE EXTENSION IF NOT EXISTS pgcrypto;
--

CREATE TABLE IF NOT EXISTS market_raw (
    id               BIGSERIAL PRIMARY KEY,
    channel          TEXT NOT NULL,                                      -- 原始頻道，如 trades/orderbook
    symbol           TEXT NOT NULL,                                      -- TAIFEX 代號（含月份/週別）
    event_seq        BIGINT,                                             -- 交易所或廠商提供的序號
    checksum         TEXT,                                               -- 原始帧提供的 checksum 或自行計算
    event_ts_utc     TIMESTAMPTZ NOT NULL,
    event_ts_local   TIMESTAMPTZ NOT NULL,
    payload          JSONB NOT NULL,                                     -- 完整原始 JSON
    receive_latency_ms INTEGER,                                          -- WS 到達與落地的延遲（毫秒）
    ingest_ts_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dedup_token      TEXT GENERATED ALWAYS AS (                          -- 近似 exactly-once；需 pgcrypto
        CASE
            WHEN event_seq IS NOT NULL THEN
                symbol || '|' || channel || '|' || event_seq::TEXT
            WHEN checksum IS NOT NULL THEN
                symbol || '|' || channel || '|' || checksum
            ELSE
                symbol || '|' || channel || '|' ||
                COALESCE((payload #>> '{id}'), '') || '|' ||
                date_part('epoch', event_ts_utc)::BIGINT::TEXT
        END
    ) STORED
);

CREATE UNIQUE INDEX IF NOT EXISTS market_raw_symbol_seq_uidx
    ON market_raw (symbol, channel, event_seq)
    WHERE event_seq IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS market_raw_dedup_uidx
    ON market_raw (dedup_token);

CREATE INDEX IF NOT EXISTS market_raw_ts_idx
    ON market_raw (symbol, event_ts_utc DESC);

-- -----------------------------------------------------------------------------
-- L2 深度資料（拆成每一檔）

CREATE TABLE IF NOT EXISTS market_l2 (
    symbol           TEXT NOT NULL,
    event_ts_utc     TIMESTAMPTZ NOT NULL,
    event_ts_local   TIMESTAMPTZ NOT NULL,
    level            SMALLINT NOT NULL CHECK (level >= 1),               -- 第幾檔，1 表示最優價
    bid_px           NUMERIC(18, 4),
    bid_sz           NUMERIC(18, 2),
    ask_px           NUMERIC(18, 4),
    ask_sz           NUMERIC(18, 2),
    mid_px           NUMERIC(18, 4),
    book_seq         BIGINT,
    is_snapshot      BOOLEAN NOT NULL DEFAULT FALSE,
    channel          TEXT NOT NULL DEFAULT 'orderbook',
    checksum         TEXT,
    ingest_ts_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, event_ts_utc, level, is_snapshot)
);

CREATE UNIQUE INDEX IF NOT EXISTS market_l2_seq_uidx
    ON market_l2 (symbol, book_seq, level)
    WHERE book_seq IS NOT NULL;

CREATE INDEX IF NOT EXISTS market_l2_symbol_ts_idx
    ON market_l2 (symbol, event_ts_utc DESC, level);

CREATE INDEX IF NOT EXISTS market_l2_snapshot_idx
    ON market_l2 (symbol, is_snapshot, event_ts_utc DESC);

-- -----------------------------------------------------------------------------
-- 成交資料

CREATE TABLE IF NOT EXISTS market_trades (
    symbol           TEXT NOT NULL,
    trade_id         TEXT NOT NULL,                                      -- 原始成交編號或 seq
    event_seq        BIGINT,
    side             TEXT,                                               -- buy/sell 或留空
    price            NUMERIC(18, 4) NOT NULL,
    quantity         NUMERIC(18, 2) NOT NULL,
    turnover         NUMERIC(24, 4),
    event_ts_utc     TIMESTAMPTZ NOT NULL,
    event_ts_local   TIMESTAMPTZ NOT NULL,
    channel          TEXT NOT NULL DEFAULT 'trades',
    checksum         TEXT,
    ingest_ts_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_payload_id BIGINT REFERENCES market_raw (id) ON DELETE SET NULL,
    PRIMARY KEY (symbol, trade_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS market_trades_seq_uidx
    ON market_trades (symbol, event_seq)
    WHERE event_seq IS NOT NULL;

CREATE INDEX IF NOT EXISTS market_trades_symbol_ts_idx
    ON market_trades (symbol, event_ts_utc DESC);

-- -----------------------------------------------------------------------------
-- 彙總行情（Tick / Quote）

CREATE TABLE IF NOT EXISTS market_quotes (
    symbol           TEXT NOT NULL,
    event_ts_utc     TIMESTAMPTZ NOT NULL,
    event_ts_local   TIMESTAMPTZ NOT NULL,
    last_px          NUMERIC(18, 4),
    prev_close_px    NUMERIC(18, 4),
    open_px          NUMERIC(18, 4),
    high_px          NUMERIC(18, 4),
    low_px           NUMERIC(18, 4),
    bid_px_1         NUMERIC(18, 4),
    bid_sz_1         NUMERIC(18, 2),
    ask_px_1         NUMERIC(18, 4),
    ask_sz_1         NUMERIC(18, 2),
    volume           NUMERIC(20, 2),
    turnover         NUMERIC(24, 4),
    open_interest    NUMERIC(18, 2),
    implied_vol      NUMERIC(10, 6),
    est_settlement   NUMERIC(18, 4),
    book_seq         BIGINT,
    checksum         TEXT,
    ingest_ts_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, event_ts_utc)
);

CREATE UNIQUE INDEX IF NOT EXISTS market_quotes_seq_uidx
    ON market_quotes (symbol, book_seq)
    WHERE book_seq IS NOT NULL;

CREATE INDEX IF NOT EXISTS market_quotes_symbol_ts_idx
    ON market_quotes (symbol, event_ts_utc DESC);

-- -----------------------------------------------------------------------------
-- 對帳 / 補洞紀錄

CREATE TABLE IF NOT EXISTS reconcile_log (
    id               BIGSERIAL PRIMARY KEY,
    symbol           TEXT NOT NULL,
    channel          TEXT NOT NULL,
    start_seq        BIGINT,
    end_seq          BIGINT,
    start_ts_utc     TIMESTAMPTZ,
    end_ts_utc       TIMESTAMPTZ,
    gap_detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gap_status       TEXT NOT NULL DEFAULT 'pending',                     -- pending / backfilled / ignored
    notes            TEXT,
    last_retry_at    TIMESTAMPTZ,
    retry_count      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS reconcile_log_symbol_status_idx
    ON reconcile_log (symbol, channel, gap_status);

-- -----------------------------------------------------------------------------
-- 回補後補寫紀錄，追蹤 ETL 進度（可搭配 tools/verify_gap.py）

CREATE TABLE IF NOT EXISTS backfill_watermark (
    symbol           TEXT NOT NULL,
    channel          TEXT NOT NULL,
    last_seq         BIGINT,
    last_ts_utc      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, channel)
);

-- -----------------------------------------------------------------------------
-- 寫入效能建議
--   1. COPY 實作：建議使用 `\copy market_raw (columns...) FROM STDIN CSV` 或 psycopg 二進位協定。
--   2. 單批 500~2000 筆，避免鎖表；同時使用 UNLOGGED 暫時表 staging 再合併主表。
--   3. 定期 VACUUM/ANALYZE，並針對 `market_l2` 的時間區間查詢採用 BRIN 索引以降低儲存。
--      例如：CREATE INDEX IF NOT EXISTS market_l2_ts_brin ON market_l2 USING brin (event_ts_utc);
--   4. 關鍵查詢：`EXPLAIN (ANALYZE, BUFFERS)` 應顯示命中 `market_l2_symbol_ts_idx` 或對應索引。
--
