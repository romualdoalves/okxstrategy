"""
database.py — Modelos SQLAlchemy + sessão PostgreSQL.
"""

from __future__ import annotations
import datetime
import os
import logging

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, JSON, String, Text, UniqueConstraint, create_engine, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

log = logging.getLogger("database")

def _resolve_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://crypto:crypto@localhost:5432/okx_strategy",
    )


DATABASE_URL = _resolve_database_url()
log.info("Database URL: %s", DATABASE_URL)

engine = create_engine(
    DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
               .replace("postgres://",    "postgresql+psycopg://", 1),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# ── Modelos ──────────────────────────────────────────────────────────────────

class BotModel(Base):
    __tablename__ = "bots"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    name            = Column(String,  nullable=False)
    strategy_id     = Column(String,  nullable=False)
    exchange        = Column(String,  default="okx")
    symbol          = Column(String,  nullable=False, default="BTC-USDT", unique=True)
    timeframe       = Column(String,  default="15m")
    demo            = Column(Boolean, default=True)
    stake_usd       = Column(Float,   default=100.0)
    leverage        = Column(Integer, default=1)
    strategy_params = Column(JSON,    default={})
    active          = Column(Boolean, default=False)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)

    # Risk
    stop_loss_usd    = Column(Float, default=-50.0)
    # Saldo spot do ativo no momento da criação do bot — usado para isolar
    # holdings pré-existentes do usuário da detecção de divergências.
    baseline_balance = Column(Float, default=0.0)


class TradeModel(Base):
    __tablename__ = "trades"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    bot_id      = Column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String)   # "entry" | "tp1" | "exit"
    event       = Column(String)   # "SL" | "TP1_50pct" | "TRAILING" | "MANUAL"
    direction   = Column(String)   # "LONG" | "SHORT"
    symbol      = Column(String)
    size        = Column(Integer)
    entry_price = Column(Float,  nullable=True)
    exit_price  = Column(Float,  nullable=True)
    sl_price    = Column(Float,  nullable=True)
    tp1_price   = Column(Float,  nullable=True)
    peak_price  = Column(Float,  nullable=True)   # maior excursão favorável (Long) / menor (Short) desde a entrada
    tp1_done    = Column(Boolean, default=False)
    atr         = Column(Float,  nullable=True)
    pnl         = Column(Float,  nullable=True)   # P&L bruto (sem taxas)
    fee         = Column(Float,  nullable=True)   # Corretagem total (entrada + saída); NULL = ainda não sincronizado
    daily_pnl   = Column(Float,  nullable=True)
    source      = Column(String, default="bot")   # "bot" | "webhook"
    timestamp   = Column(DateTime, default=datetime.datetime.utcnow)
    closed_at   = Column(DateTime, nullable=True)   # set when position closes


class BotSnapshotModel(Base):
    """Estado do bot a cada candle fechado — para equity curve."""
    __tablename__ = "snapshots"

    id        = Column(Integer,  primary_key=True, autoincrement=True)
    bot_id    = Column(Integer,  ForeignKey("bots.id", ondelete="CASCADE"), nullable=False)
    equity    = Column(Float,    nullable=False)
    daily_pnl = Column(Float,    default=0.0)
    wins      = Column(Integer,  default=0)
    losses    = Column(Integer,  default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class SignalLogModel(Base):
    """Snapshot completo de cada avaliação de sinal — para análise e optimização."""
    __tablename__ = "signal_logs"

    id                   = Column(Integer,  primary_key=True, autoincrement=True)
    bot_id               = Column(Integer,  ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp            = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    signal               = Column(String)              # "buy" | "sell" | "hold"
    hold_reason          = Column(String,  nullable=True)
    indicators           = Column(JSON,    default={}) # valores numéricos de todos os indicadores
    meta                 = Column(JSON,    default={}) # SL, TP1, regime_state, etc.
    candle_open          = Column(Float,   nullable=True)
    candle_high          = Column(Float,   nullable=True)
    candle_low           = Column(Float,   nullable=True)
    candle_close         = Column(Float,   nullable=True)
    candle_volume        = Column(Float,   nullable=True)
    resulted_in_trade_id = Column(Integer, ForeignKey("trades.id"), nullable=True)


class TradeReportModel(Base):
    """Relatório gerado por IA para um trade finalizado — persistido para reutilização."""
    __tablename__ = "trade_reports"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    trade_id    = Column(Integer,  ForeignKey("trades.id", ondelete="CASCADE"), unique=True, nullable=False)
    bot_id      = Column(Integer,  ForeignKey("bots.id",   ondelete="CASCADE"), nullable=False, index=True)
    ai_analysis = Column(Text,     nullable=True)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)


class AiAnalysisLogModel(Base):
    """Histórico de consultas da IA (Segunda Opinião) para o BotDetail."""
    __tablename__ = "ai_analysis_logs"

    id        = Column(Integer,  primary_key=True, autoincrement=True)
    bot_id    = Column(Integer,  ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    prompt    = Column(Text,     nullable=False)
    response  = Column(Text,     nullable=False)


class SettingsModel(Base):
    """Configurações persistentes da plataforma — credenciais armazenadas com criptografia Fernet."""
    __tablename__ = "settings"

    key        = Column(String,   primary_key=True)   # ex: "okx_api_key", "okx_api_secret"
    value      = Column(Text,     nullable=False)      # valor criptografado (Fernet token)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class StrategyModel(Base):
    """Todas as estratégias disponíveis (nativas + Factory AI)."""
    __tablename__ = "strategies"

    id               = Column(Integer,  primary_key=True, autoincrement=True)
    strategy_id      = Column(String,   nullable=False, unique=True, index=True)  # ex: TF001, PA007
    name             = Column(String,   nullable=False)
    description      = Column(Text,     nullable=True)
    source_text      = Column(Text,     nullable=True)   # texto original do usuário
    plan_json        = Column(JSON,     default={})      # plano aprovado
    code_py          = Column(Text,     nullable=True)   # código gerado e validado
    status           = Column(String,   default="deployed")  # draft|deployed|disabled
    created_at       = Column(DateTime, default=datetime.datetime.utcnow)
    deployed_at      = Column(DateTime, nullable=True)
    validation_report = Column(JSON,    default={})


class OrderRejectionModel(Base):
    """Auditoria de ordens recusadas/falhas críticas retornadas pela exchange."""
    __tablename__ = "order_rejections"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    bot_id           = Column(Integer, ForeignKey("bots.id", ondelete="SET NULL"), nullable=True, index=True)
    bot_name         = Column(String, nullable=True)
    symbol           = Column(String, nullable=True, index=True)
    side             = Column(String, nullable=True)
    order_type       = Column(String, nullable=True)
    ord_id           = Column(String, nullable=True, index=True)
    algo_id          = Column(String, nullable=True, index=True)
    status           = Column(String, default="open", index=True)
    reason           = Column(Text, nullable=True)
    raw_payload      = Column(JSON, default={})
    resolved         = Column(Boolean, default=False, index=True)
    resolution_notes = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at       = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class AutoScanHistoryModel(Base):
    """Histórico salvo de Auto-Scans para criação sequencial de bots."""
    __tablename__ = "auto_scan_history"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    results    = Column(JSON,     default=[])  # Lista de combinações INICIAR


class TrackedSymbolModel(Base):
    """Ativos spot OKX rastreados pelo serviço de Market Data."""
    __tablename__ = "tracked_symbols"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    symbol     = Column(String,  nullable=False, unique=True, index=True)
    timeframes = Column(JSON,    default=["15m"])  # Lista de timeframes OKX
    last_sync  = Column(DateTime, nullable=True)
    is_active  = Column(Boolean, default=True)


class HistoricCandleModel(Base):
    """Candles históricos persistidos localmente para scanner/backtests."""
    __tablename__ = "historic_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "epoch", name="uq_historic_candles"),
    )

    id        = Column(Integer, primary_key=True, autoincrement=True)
    symbol    = Column(String,  nullable=False, index=True)
    timeframe = Column(String,  nullable=False, index=True)
    epoch     = Column(DateTime, nullable=False, index=True)
    open      = Column(Float,   nullable=False)
    high      = Column(Float,   nullable=False)
    low       = Column(Float,   nullable=False)
    close     = Column(Float,   nullable=False)
    volume    = Column(Float,   nullable=False)


class MarketDataSyncJobModel(Base):
    """Histórico de jobs de sincronização de Market Data."""
    __tablename__ = "market_data_sync_jobs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    batch_id       = Column(String,  nullable=True, index=True)
    symbol         = Column(String,  nullable=False, index=True)
    timeframe      = Column(String,  nullable=True, index=True)
    trigger        = Column(String,  nullable=False, default="manual")
    status         = Column(String,  nullable=False, default="queued", index=True)  # queued|running|success|failed
    candles_synced = Column(Integer, nullable=True)
    error          = Column(Text,    nullable=True)
    created_at     = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    started_at     = Column(DateTime, nullable=True)
    finished_at    = Column(DateTime, nullable=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
    # Migrações incrementais — seguro rodar múltiplas vezes (IF NOT EXISTS)
    _run_migrations()


def _run_migrations():
    """Aplica colunas novas que create_all não adiciona automaticamente em tabelas existentes."""
    migrations = [
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fee FLOAT",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS peak_price FLOAT",
        """
        CREATE TABLE IF NOT EXISTS factory_strategies (
            id SERIAL PRIMARY KEY,
            strategy_id VARCHAR(10) UNIQUE NOT NULL,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            source_text TEXT,
            plan_json JSON DEFAULT '{}'::json,
            code_py TEXT,
            status VARCHAR(20) DEFAULT 'deployed',
            created_at TIMESTAMP DEFAULT NOW(),
            deployed_at TIMESTAMP,
            validation_report JSON DEFAULT '{}'::json
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_factory_strategies_strategy_id ON factory_strategies(strategy_id)",
        "CREATE TABLE IF NOT EXISTS settings (key VARCHAR PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT NOW())",
        """
        CREATE TABLE IF NOT EXISTS order_rejections (
            id SERIAL PRIMARY KEY,
            bot_id INTEGER NULL REFERENCES bots(id) ON DELETE SET NULL,
            bot_name VARCHAR NULL,
            symbol VARCHAR NULL,
            side VARCHAR NULL,
            order_type VARCHAR NULL,
            ord_id VARCHAR NULL,
            algo_id VARCHAR NULL,
            status VARCHAR DEFAULT 'open',
            reason TEXT NULL,
            raw_payload JSON DEFAULT '{}'::json,
            resolved BOOLEAN DEFAULT FALSE,
            resolution_notes TEXT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_order_rejections_bot_id ON order_rejections(bot_id)",
        "CREATE INDEX IF NOT EXISTS idx_order_rejections_symbol ON order_rejections(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_order_rejections_resolved ON order_rejections(resolved)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_bots_symbol_upper ON bots (UPPER(symbol))",
        "ALTER TABLE bots ADD COLUMN IF NOT EXISTS baseline_balance FLOAT DEFAULT 0.0",
        """
        CREATE TABLE IF NOT EXISTS auto_scan_history (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP DEFAULT NOW(),
            results JSON DEFAULT '[]'::json
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_auto_scan_history_created_at ON auto_scan_history(created_at)",
        """
        CREATE TABLE IF NOT EXISTS tracked_symbols (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR NOT NULL UNIQUE,
            timeframes JSON DEFAULT '["15m"]'::json,
            last_sync TIMESTAMP NULL,
            is_active BOOLEAN DEFAULT TRUE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tracked_symbols_symbol ON tracked_symbols(symbol)",
        """
        CREATE TABLE IF NOT EXISTS historic_candles (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR NOT NULL,
            timeframe VARCHAR NOT NULL,
            epoch TIMESTAMP NOT NULL,
            open FLOAT NOT NULL,
            high FLOAT NOT NULL,
            low FLOAT NOT NULL,
            close FLOAT NOT NULL,
            volume FLOAT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_historic_candles ON historic_candles(symbol, timeframe, epoch)",
        "CREATE INDEX IF NOT EXISTS idx_historic_candles_symbol_tf_epoch ON historic_candles(symbol, timeframe, epoch)",
        """
        CREATE TABLE IF NOT EXISTS market_data_sync_jobs (
            id SERIAL PRIMARY KEY,
            batch_id VARCHAR NULL,
            symbol VARCHAR NOT NULL,
            timeframe VARCHAR NULL,
            trigger VARCHAR NOT NULL DEFAULT 'manual',
            status VARCHAR NOT NULL DEFAULT 'queued',
            candles_synced INTEGER NULL,
            error TEXT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            started_at TIMESTAMP NULL,
            finished_at TIMESTAMP NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_market_data_sync_jobs_created_at ON market_data_sync_jobs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_market_data_sync_jobs_batch_id ON market_data_sync_jobs(batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_market_data_sync_jobs_status ON market_data_sync_jobs(status)",
    ]
    try:
        with engine.connect() as conn:
            for sql in migrations:
                conn.execute(text(sql))
            conn.commit()
    except Exception as exc:
        import logging as _log
        _log.getLogger("database").warning("Migration parcial: %s", exc)
