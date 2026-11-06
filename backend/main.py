"""Minimal persistent API foundation for an authorized normalized candle feed.

No market-data provider is configured here. Feed adapters must only submit data
they are licensed to use and redistribute.
"""
from __future__ import annotations

import os
import sqlite3
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
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


class UpstoxSyncRequest(BaseModel):
    exchange: Literal["NSE", "BSE"] = "NSE"
    interval: Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h"] = "5m"
    symbols: list[str] = Field(min_length=1, max_length=30)


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


def upstox_request(path: str, params: dict[str, str] | None = None) -> dict:
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "Upstox is not configured. Add UPSTOX_ACCESS_TOKEN to Vercel Environment Variables.")
    url = "https://api.upstox.com" + path
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + token})
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = "Upstox rejected the request. Check that the access token is valid and not expired."
        if exc.code == 429:
            detail = "Upstox rate limit reached. Wait briefly and try again."
        raise HTTPException(502, detail) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Could not reach Upstox market data. Try again shortly.") from exc


_NSE_CACHE = {"fetched_at": 0.0, "overview": None}
_NSE_MCP_URL = "https://mcp.nseindia.in/cmmkt/mcp"


def _mcp_payload(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    data = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    if not data:
        raise ValueError("NSE MCP returned an empty response")
    return json.loads(data[-1])


def nse_mcp_call(tool_name: str, arguments: dict) -> dict:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "User-Agent": "INTRADAY-AI/1.0 (market research dashboard)",
               "Referer": "https://www.nseindia.com/"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "intraday-ai-research", "version": "1.0"}}}
    try:
        request = Request(_NSE_MCP_URL, data=json.dumps(init).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=8) as response:
            session_id = response.headers.get("Mcp-Session-Id")
            initialized = _mcp_payload(response.read().decode("utf-8"))
        if initialized.get("error") or not session_id:
            raise ValueError("NSE MCP initialization failed")
        headers["Mcp-Session-Id"] = session_id
        negotiated_version = initialized.get("result", {}).get("protocolVersion", "2025-03-26")
        headers["Mcp-Protocol-Version"] = negotiated_version
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments}}
        request = Request(_NSE_MCP_URL, data=json.dumps(call).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=8) as response:
            envelope = _mcp_payload(response.read().decode("utf-8"))
        if envelope.get("error"):
            raise ValueError("NSE MCP tool call failed")
        result = envelope.get("result", {})
        if result.get("isError"):
            raise ValueError("NSE MCP returned a data error")
        text_blocks = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
        if not text_blocks:
            raise ValueError("NSE MCP returned no data")
        return json.loads(text_blocks[0])
    except HTTPError as exc:
        try:
            upstream_detail = exc.read(240).decode("utf-8", errors="replace").strip()
        except Exception:
            upstream_detail = ""
        detail = f"NSE public market snapshot is unavailable (HTTP {exc.code})."
        if upstream_detail:
            detail += " " + upstream_detail[:180]
        raise HTTPException(502, detail) from exc
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(502, "NSE public market snapshot is temporarily unavailable.") from exc


@app.get("/api/nse/status")
def nse_status():
    return {"provider": "NSE official MCP", "configured": True, "mode": "delayed-snapshot",
            "delay": "approximately 1–3 minutes", "api_key_required": False,
            "signal_eligible": False,
            "message": "Informational market snapshots only; this source does not provide intraday candles."}


@app.get("/api/nse/overview")
def nse_overview():
    now = time.monotonic()
    cached = _NSE_CACHE.get("overview")
    if cached and now - _NSE_CACHE["fetched_at"] < 60:
        return cached
    movers = {}
    errors = []
    try:
        movers = nse_mcp_call("nse_get_market_movers", {"indexName": "", "limit": 5})
    except HTTPException as exc:
        errors.append("Market movers: " + str(exc.detail))
    overview = {"provider": "NSE official MCP", "delay": "approximately 1–3 minutes",
                "informational_only": True, "intraday_candles": False,
                "available": bool(movers), "errors": errors,
                "indices": [], "index_timestamp": None,
                "movers": movers, "source_timestamp": movers.get("gainersUpdatedAt") or movers.get("losersUpdatedAt"),
                "warning": "Delayed informational data only. Not for trading signals or investment advice."}
    _NSE_CACHE["fetched_at"], _NSE_CACHE["overview"] = now, overview
    return overview


@app.get("/api", include_in_schema=False)
def service_home():
    return {"service": "INTRADAY AI API", "docs": "/docs", "health": "/api/health"}


@app.get("/api/health", include_in_schema=False)
def api_health():
    return health()


@app.get("/health")
def health():
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS count, MAX(received_at) AS latest FROM candles").fetchone()
    configured = bool(os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip())
    return {"status": "ok", "data_mode": "upstox-configured" if configured else "nse-delayed-snapshot",
            "stored_candles": row["count"], "last_received_at": row["latest"],
            "market_data_source": "Upstox (token configured)" if configured else "NSE official delayed snapshots",
            "provider_configured": configured, "free_snapshot_available": True, "live_data": False,
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


@app.get("/api/upstox/status")
def upstox_status():
    configured = bool(os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip())
    return {"provider": "Upstox", "configured": configured,
            "status": "configured" if configured else "credentials_required",
            "required_environment_variable": "UPSTOX_ACCESS_TOKEN",
            "message": "Token configured; import current-session candles on demand." if configured else "Generate an Upstox access token and add it as UPSTOX_ACCESS_TOKEN in Vercel Production."}


@app.post("/api/upstox/sync")
def upstox_sync(payload: UpstoxSyncRequest):
    if not os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip():
        raise HTTPException(503, "Add UPSTOX_ACCESS_TOKEN to Vercel Environment Variables, then redeploy.")
    minute_intervals = {"1m": "1", "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "1h": "60"}
    interval = minute_intervals[payload.interval]
    now = datetime.now(timezone.utc).isoformat()
    saved, synced, skipped = 0, [], []
    for raw_symbol in payload.symbols:
        symbol = raw_symbol.strip().upper()
        if not symbol or len(symbol) > 40:
            continue
        search = upstox_request("/v2/instruments/search", {
            "query": symbol, "exchanges": payload.exchange, "segments": "EQ", "records": "30"
        })
        matches = [item for item in search.get("data", [])
                   if item.get("exchange") == payload.exchange and
                   item.get("trading_symbol", "").upper() == symbol and
                   item.get("segment") in ("NSE_EQ", "BSE_EQ")]
        if not matches:
            skipped.append({"symbol": symbol, "reason": "No exact equity instrument match"})
            continue
        key = matches[0]["instrument_key"]
        encoded_key = quote(key, safe="")
        candles_url = "/v3/historical-candle/intraday/" + encoded_key + "/minutes/" + interval
        data = upstox_request(candles_url)
        candles = data.get("data", {}).get("candles", [])
        if not candles:
            skipped.append({"symbol": symbol, "reason": "Upstox returned no candles for the current session"})
            continue
        with db() as conn:
            for candle in candles:
                ts = datetime.fromisoformat(candle[0].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    skipped.append({"symbol": symbol, "reason": "Upstox candle had no timezone"})
                    continue
                conn.execute("""INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(exchange,symbol,interval,ts) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                    volume=excluded.volume, source=excluded.source, received_at=excluded.received_at""",
                    (payload.exchange, symbol, payload.interval, ts.astimezone(timezone.utc).isoformat(),
                     float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4]),
                     float(candle[5]) if candle[5] is not None else None, "Upstox REST", now))
                saved += 1
        synced.append(symbol)
    return {"provider": "Upstox", "accepted": saved, "synced_symbols": synced,
            "skipped": skipped, "interval": payload.interval, "received_at": now,
            "storage_warning": "Vercel SQLite in /tmp is temporary; configure managed PostgreSQL for durable history."}


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


@app.get("/api/instruments")
def instruments(exchange: Literal["NSE", "BSE"] | None = None, interval: str | None = None):
    query = "SELECT exchange, symbol, MAX(ts) AS latest FROM candles"
    filters, args = [], []
    if exchange:
        filters.append("exchange=?")
        args.append(exchange)
    if interval:
        filters.append("interval=?")
        args.append(interval)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " GROUP BY exchange, symbol ORDER BY symbol"
    with db() as conn:
        rows = conn.execute(query, args).fetchall()
    return {"instruments": [{"exchange": r["exchange"], "symbol": r["symbol"], "last_candle_at": r["latest"]} for r in rows]}


@app.get("/api/scan")
def scan(exchange: Literal["NSE", "BSE"] = "NSE", interval: str = "5m", min_relative_volume: float = 0):
    with db() as conn:
        symbols = conn.execute("SELECT DISTINCT symbol FROM candles WHERE exchange=? AND interval=? ORDER BY symbol", (exchange, interval)).fetchall()
    results = []
    for item in symbols:
        symbol = item["symbol"]
        with db() as conn:
            rows = conn.execute("""SELECT * FROM (SELECT * FROM candles WHERE exchange=? AND symbol=? AND interval=? ORDER BY ts DESC LIMIT 250) ORDER BY ts ASC""", (exchange, symbol, interval)).fetchall()
        if not rows:
            continue
        closes = [r["close"] for r in rows]
        e9, e20 = ema(closes, 9), ema(closes, 20)
        stance = "WAIT"
        if e9 is not None and e20 is not None and closes[-1] > e9 > e20:
            stance = "BUY"
        elif e9 is not None and e20 is not None and closes[-1] < e9 < e20:
            stance = "SELL"
        volumes = [r["volume"] for r in rows if r["volume"] is not None]
        relative_volume = None
        if len(volumes) >= 21 and sum(volumes[-21:-1]) > 0:
            relative_volume = volumes[-1] / (sum(volumes[-21:-1]) / 20)
        if relative_volume is not None and relative_volume < min_relative_volume:
            continue
        atr = None
        if len(rows) >= 15:
            trs = [max(rows[j]["high"]-rows[j]["low"], abs(rows[j]["high"]-rows[j-1]["close"]), abs(rows[j]["low"]-rows[j-1]["close"])) for j in range(1, len(rows))]
            atr = sum(trs[-14:]) / 14
        results.append({"exchange": exchange, "symbol": symbol, "close": closes[-1],
            "research_stance": stance, "entry_valid": False, "rsi14": rsi(closes),
            "atr14": atr, "relative_volume": relative_volume, "source": rows[-1]["source"],
            "timestamp": rows[-1]["ts"]})
    return {"results": results, "live_data": False, "note": "Stored candles only; live entry unavailable."}


class WatchItem(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: Literal["NSE", "BSE"]
    notes: str = Field(default="", max_length=200)


@app.get("/api/watchlist")
def get_watchlist():
    with db() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    return {"watchlist": [dict(row) for row in rows]}


@app.post("/api/watchlist")
def add_watchlist(item: WatchItem):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute("""INSERT INTO watchlist(exchange,symbol,notes,added_at) VALUES(?,?,?,?)
            ON CONFLICT(exchange,symbol) DO UPDATE SET notes=excluded.notes""",
            (item.exchange, item.symbol.upper(), item.notes, now))
    return {"ok": True, "exchange": item.exchange, "symbol": item.symbol.upper()}


@app.delete("/api/watchlist/{symbol}")
def delete_watchlist(symbol: str, exchange: Literal["NSE", "BSE"] = "NSE"):
    with db() as conn:
        conn.execute("DELETE FROM watchlist WHERE exchange=? AND symbol=?", (exchange, symbol.upper()))
    return {"ok": True}

