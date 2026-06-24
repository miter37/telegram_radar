-- Market Radar Desktop — schema v1
-- All 8 tables from the development plan, plus FTS5 for message_text search.
-- Phase 0 uses 5 tables; the rest are created empty for future migration-free use.

PRAGMA foreign_keys = ON;

-- 1. Raw feeds (immutable original messages)
CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    message_text TEXT NOT NULL,
    message_url TEXT,
    raw_hash TEXT UNIQUE NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feed_items_channel ON feed_items(channel_id);
CREATE INDEX IF NOT EXISTS idx_feed_items_datetime ON feed_items(datetime DESC);

-- 2. LLM raw outputs (for prompt iteration and reprocessing)
CREATE TABLE IF NOT EXISTS llm_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    parsed_ok INTEGER NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(feed_id) REFERENCES feed_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_llm_extractions_feed ON llm_extractions(feed_id);
CREATE INDEX IF NOT EXISTS idx_llm_extractions_parsed ON llm_extractions(parsed_ok);

-- 3. Signals (the main view of the live feed table)
CREATE TABLE IF NOT EXISTS feed_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL UNIQUE,
    date TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    topic TEXT NOT NULL,
    main_content TEXT NOT NULL,
    importance_score INTEGER NOT NULL,
    interest_score INTEGER NOT NULL,
    should_alert INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(feed_id) REFERENCES feed_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feed_signals_date ON feed_signals(date DESC);
CREATE INDEX IF NOT EXISTS idx_feed_signals_importance ON feed_signals(importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_feed_signals_interest ON feed_signals(interest_score DESC);

-- 4. Canonical tag dictionary (user-editable)
CREATE TABLE IF NOT EXISTS canonical_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    tag_group TEXT NOT NULL,
    aliases TEXT,
    ticker TEXT,
    sector TEXT,
    parent_tag_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(parent_tag_id) REFERENCES canonical_tags(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_canonical_tags_group ON canonical_tags(tag_group);

-- 5. Per-feed canonical tag links
CREATE TABLE IF NOT EXISTS signal_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL,
    signal_id INTEGER NOT NULL,
    canonical_tag_id INTEGER NOT NULL,
    canonical_name TEXT NOT NULL,
    tag_group TEXT NOT NULL,
    normalize_confidence REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(feed_id) REFERENCES feed_items(id) ON DELETE CASCADE,
    FOREIGN KEY(signal_id) REFERENCES feed_signals(id) ON DELETE CASCADE,
    FOREIGN KEY(canonical_tag_id) REFERENCES canonical_tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_signal_tags_signal ON signal_tags(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_tags_canonical ON signal_tags(canonical_tag_id);
CREATE INDEX IF NOT EXISTS idx_signal_tags_name ON signal_tags(canonical_name);

-- 6. Topic clusters (similar feeds grouped)
CREATE TABLE IF NOT EXISTS topic_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    canonical_tags TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    feed_count INTEGER NOT NULL DEFAULT 0,
    cluster_score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_clusters_topic ON topic_clusters(topic);
CREATE INDEX IF NOT EXISTS idx_topic_clusters_last_seen ON topic_clusters(last_seen_at DESC);

-- 7. Daily topics (top topics per day)
CREATE TABLE IF NOT EXISTS daily_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    topic_cluster_id INTEGER NOT NULL,
    daily_rank INTEGER NOT NULL,
    summary TEXT NOT NULL,
    representative_feed_ids TEXT NOT NULL,
    total_score REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(topic_cluster_id) REFERENCES topic_clusters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_daily_topics_date ON daily_topics(date DESC);

-- 8. Tag flow metrics (per tag per day)
CREATE TABLE IF NOT EXISTS tag_flow_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    feed_count INTEGER NOT NULL,
    avg_importance REAL NOT NULL,
    avg_interest REAL NOT NULL,
    velocity REAL NOT NULL,
    acceleration REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(tag_id) REFERENCES canonical_tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tag_flow_metrics_tag_date ON tag_flow_metrics(tag_id, date);

-- FTS5 virtual table for message text search (Phase 1+)
CREATE VIRTUAL TABLE IF NOT EXISTS feed_items_fts USING fts5(
    message_text,
    channel_name,
    content='feed_items',
    content_rowid='id',
    tokenize='unicode61'
);

-- Triggers to keep FTS in sync with feed_items
CREATE TRIGGER IF NOT EXISTS feed_items_ai AFTER INSERT ON feed_items BEGIN
    INSERT INTO feed_items_fts(rowid, message_text, channel_name)
    VALUES (new.id, new.message_text, new.channel_name);
END;

CREATE TRIGGER IF NOT EXISTS feed_items_ad AFTER DELETE ON feed_items BEGIN
    INSERT INTO feed_items_fts(feed_items_fts, rowid, message_text, channel_name)
    VALUES('delete', old.id, old.message_text, old.channel_name);
END;

CREATE TRIGGER IF NOT EXISTS feed_items_au AFTER UPDATE ON feed_items BEGIN
    INSERT INTO feed_items_fts(feed_items_fts, rowid, message_text, channel_name)
    VALUES('delete', old.id, old.message_text, old.channel_name);
    INSERT INTO feed_items_fts(rowid, message_text, channel_name)
    VALUES (new.id, new.message_text, new.channel_name);
END;

-- 9. ingest state: per-channel last seen message id (for history backfill)
CREATE TABLE IF NOT EXISTS ingest_state (
    channel_id INTEGER PRIMARY KEY,
    channel_username TEXT,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_fetched_at TEXT NOT NULL,
    total_fetched INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingest_state_username ON ingest_state(channel_username);

-- 10. market bars (yfinance OHLCV cache)
CREATE TABLE IF NOT EXISTS market_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    fetched_at TEXT NOT NULL,
    UNIQUE(ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_market_bars_ticker_date ON market_bars(ticker, date);

-- 11. feed→ticker links (for cross-validating feeds against price moves)
CREATE TABLE IF NOT EXISTS feed_ticker_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL,
    signal_id INTEGER,
    ticker TEXT NOT NULL,
    ticker_name TEXT,
    confidence REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(feed_id) REFERENCES feed_items(id) ON DELETE CASCADE,
    FOREIGN KEY(signal_id) REFERENCES feed_signals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feed_ticker_links_ticker ON feed_ticker_links(ticker);
CREATE INDEX IF NOT EXISTS idx_feed_ticker_links_feed ON feed_ticker_links(feed_id);

-- 12. daily reports (LLM-generated, optionally sent to a bot)
CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL UNIQUE,
    title TEXT,
    body TEXT NOT NULL,
    payload_json TEXT,
    sent_to_bot INTEGER NOT NULL DEFAULT 0,
    bot_chat_id TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);

-- 13. daily topic clusters (Stage 1: LLM clustering result)
CREATE TABLE IF NOT EXISTS daily_topic_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    topic_idx INTEGER NOT NULL,
    label TEXT NOT NULL,
    member_signal_ids TEXT NOT NULL,   -- comma-separated feed_signals.id
    member_count INTEGER NOT NULL,
    avg_importance REAL NOT NULL DEFAULT 0,
    avg_interest REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(report_date, topic_idx)
);
CREATE INDEX IF NOT EXISTS idx_dtc_date ON daily_topic_clusters(report_date DESC);

-- 14. daily topic reports (Stage 2: per-topic LLM summary)
CREATE TABLE IF NOT EXISTS daily_topic_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    topic_idx INTEGER NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL,
    body_md TEXT NOT NULL,
    timeline_json TEXT,
    watchlist_json TEXT,
    member_count INTEGER NOT NULL,
    avg_importance REAL NOT NULL DEFAULT 0,
    avg_interest REAL NOT NULL DEFAULT 0,
    top_signal_ids TEXT NOT NULL,    -- comma-separated, importance DESC
    md_path TEXT,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(report_date, topic_idx)
);
CREATE INDEX IF NOT EXISTS idx_dtr_date ON daily_topic_reports(report_date DESC);
