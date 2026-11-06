"""Minimal persistent API foundation for an authorized normalized candle feed.

No market-data provider is configured here. Feed adapters must only submit data
they are licensed to use and redistribute.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# Vercel functions only have ephemeral writable storage under /tmp.
_DEFAULT_DB = Path("/tmp/intraday-ai.sqlite3") if os.environ.get("VERCEL") else Path(__file__).with_name("intraday_ai.sqlite3")
DB_PATH = Path(os.environ.get("INTRADAY_AI_DB", _DEFAULT_DB))
app = FastAPI(title="INTRADAY AI API", version="0.1.1", description="Provider-neutral market research API foundation")
# For a separately deployed frontend, set FRONTEND_ORIGINS to a comma-separated
# allowlist of exact origins. Same-origin API calls need no CORS configuration.
_frontend_origins = [origin.strip().rstrip("/") for origin in os.environ.get("FRONTEND_ORIGINS", "").split(",") if origin.strip()]
if _frontend_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_frontend_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=False,
    )


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS candles (
          exchange TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
          ts TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
          close REAL NOT NULL, volume REAL, source TEXT NOT NULL, received_at TEXT NOT NULL,
          PRIMARY KEY(exchange, symbol, interval, ts));
        CREATE INDEX IF NOT EXISTS candles_lookup ON candles(symbol, interval, ts);
        CREATE TABLE IF NOT EXISTS watchlist (
          exchange TEXT NOT NULL, symbol TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          added_at TEXT NOT NULL, PRIMARY KEY(exchange,symbol));
        CREATE TABLE IF NOT EXISTS paper_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
          side TEXT NOT NULL, quantity INTEGER NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL,
          target REAL, status TEXT NOT NULL DEFAULT 'open', exit REAL, pnl REAL,
          created_at TEXT NOT NULL, closed_at TEXT);
        """)


init_db()


class Candle(BaseModel):
    exchange: Literal["NSE", "BSE"]
    symbol: str = Field(min_length=1, max_length=40)
    interval: Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d", "1w", "1mo"]
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=100)


class CandleBatch(BaseModel):
    candles: list[Candle] = Field(min_length=1, max_length=5000)


class PaperOrder(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: Literal["NSE", "BSE"]
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)


def stamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise HTTPException(422, "Exchange candle timestamps must include a timezone")
    return dt.astimezone(timezone.utc).isoformat()


@app.get("/api", include_in_schema=False)
def service_home():
    return {"service": "INTRADAY AI API", "docs": "/docs", "health": "/api/health"}


@app.get("/api/health", include_in_schema=False)
def api_health():
    return health()


@app.get("/health")
def health():
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    return {"status": "ok", "data_mode": "provider-disconnected", "stored_candles": count,
            "market_data_source": None, "live_data": False,
            "storage": "ephemeral /tmp SQLite; use managed PostgreSQL for durable production data" if os.environ.get("VERCEL") else "local SQLite"}


@app.post("/v1/ingest/candles")
def ingest(payload: CandleBatch):
    """Idempotently persist provider-normalized candles; this endpoint is not a feed."""
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    with db() as conn:
        for c in payload.candles:
            if c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close):
                raise HTTPException(422, f"Invalid OHLC range for {c.symbol} at {c.timestamp}")
            conn.execute("""INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(exchange,symbol,interval,ts) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
              volume=excluded.volume, source=excluded.source, received_at=excluded.received_at""",
              (c.exchange, c.symbol.upper(), c.interval, stamp(c.timestamp), c.open, c.high,
               c.low, c.close, c.volume, c.source, now))
            saved += 1
    return {"accepted": saved, "idempotent_key": "exchange,symbol,interval,timestamp",
            "source": sorted({c.source for c in payload.candles}), "received_at": now}


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = value * alpha + out * (1 - alpha)
    return out


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


@app.get("/v1/analysis/{exchange}/{symbol}")
def analysis(exchange: Literal["NSE", "BSE"], symbol: str, interval: str = "5m", limit: int = 250):
    limit = max(30, min(limit, 2000))
    with db() as conn:
        rows = conn.execute("""SELECT * FROM (SELECT * FROM candles WHERE exchange=? AND symbol=? AND interval=?
          ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC""", (exchange, symbol.upper(), interval, limit)).fetchall()
    if not rows:
        raise HTTPException(404, "No candles available; provider feed is not connected")
    closes = [r["close"] for r in rows]
    typical = [(r["high"] + r["low"] + r["close"]) / 3 for r in rows]
    volumes = [r["volume"] for r in rows]
    vwap = None
    if all(v is not None for v in volumes) and sum(volumes) > 0:
        vwap = sum(p * v for p, v in zip(typical, volumes)) / sum(volumes)
    e9, e20 = ema(closes, 9), ema(closes, 20)
    stance = "WAIT"
    evidence = []
    if e9 is not None and e20 is not None:
        if closes[-1] > e9 > e20:
            evidence.append("close > EMA9 > EMA20")
        elif closes[-1] < e9 < e20:
            evidence.append("close < EMA9 < EMA20")
    if vwap is not None:
        evidence.append("close above VWAP" if closes[-1] > vwap else "close below VWAP")
    if len(evidence) >= 2 and "above" in evidence[0] and "above" in evidence[1]:
        stance = "BUY"
    elif len(evidence) >= 2 and "below" in evidence[0] and "below" in evidence[1]:
        stance = "SELL"
    else:
        evidence.append("insufficient independent confirmation; WAIT")
    return {"symbol": symbol.upper(), "exchange": exchange, "interval": interval,
            "source": rows[-1]["source"], "last_candle_at": rows[-1]["ts"],
            "received_at": rows[-1]["received_at"], "candles": len(rows),
            "close": closes[-1], "ema9": e9, "ema20": e20, "rsi14": rsi(closes),
            "vwap": vwap, "stance": stance, "evidence": evidence,
            "warning": "Analysis is based only on stored input candles. Verify source licensing and freshness."}


@app.get("/v1/candles/{exchange}/{symbol}")
def get_candles(exchange: Literal["NSE", "BSE"], symbol: str, interval: str = "5m", limit: int = 500):
    limit = max(1, min(limit, 5000))
    with db() as conn:
        rows = conn.execute("""SELECT * FROM (SELECT * FROM candles WHERE exchange=? AND symbol=? AND interval=?
          ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC""", (exchange, symbol.upper(), interval, limit)).fetchall()
    return {"exchange": exchange, "symbol": symbol.upper(), "interval": interval,
            "source": rows[-1]["source"] if rows else None, "candles": [dict(r) for r in rows]}


@app.post("/v1/paper/orders")
def create_paper_order(order: PaperOrder):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute("""INSERT INTO paper_trades(symbol,exchange,side,quantity,entry,stop,target,created_at)
          VALUES(?,?,?,?,?,?,?,?)""", (order.symbol.upper(), order.exchange, order.side,
          order.quantity, order.entry, order.stop, order.target, now))
        trade_id = cur.lastrowid
    return {"id": trade_id, "status": "open", "mode": "paper", "created_at": now,
            "note": "Simulated record only; no broker order was sent."}


@app.get("/v1/paper/trades")
def list_paper_trades():
    with db() as conn:
        rows = conn.execute("SELECT * FROM paper_trades ORDER BY id DESC").fetchall()
    return {"mode": "paper", "trades": [dict(r) for r in rows]}


app.mount('/', StaticFiles(directory=str(Path(__file__).parent / 'public'), html=True), name='dashboard')
