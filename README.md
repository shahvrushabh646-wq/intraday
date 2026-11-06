# INTRADAY AI

A standalone Indian-market research and paper-trading application. This release now includes a runnable local API, persistent SQLite storage, CSV ingestion, OHLCV indicator/strategy calculations, scanner, backtest engine, risk calculator, watchlists, alert-rule storage, and simulated trades.

## Start it

Requires Node.js 22 or later (the backend uses Node's built-in SQLite module). From this folder:

```powershell
node backend/server.mjs
```

Open <http://127.0.0.1:3000>. The app stores its database at `data/intraday-ai.sqlite`. Set `INTRADAY_AI_DATA_DIR` to keep the database outside the app folder. You can also use `npm start` when npm is installed. The server binds to loopback for local use.

## Load actual data

Use **Data Source Monitor → CSV import** to load a licensed instrument master and normalized OHLCV history. Candle CSV headers:

```text
exchange,symbol,interval,timestamp,open,high,low,close,volume
```

Supported exchange values: `NSE`, `BSE`. Intervals: `1m,3m,5m,10m,15m,30m,1h,1d,1w,1mo`. Timestamps must include a timezone. Instrument master headers:

```text
exchange,symbol,name,sector,isin,lotSize,foEligible
```

The source label is mandatory for candle imports. The app retains the source and ingestion time. It will not invent a live quote when the database has no quote. A live connection still requires a provider-specific adapter, vendor subscription, secure server-side credentials, and rights to use the feed. The normalized endpoint is `POST /api/candles`; instrument-master endpoint is `POST /api/instruments`. See `/api/health` and `/api/strategies`.

## Implemented functions

- 100 named strategy rules, with OHLCV evaluation and a `WAIT` stance when evidence is insufficient; strategies requiring external context (FII/DII, delivery, corporate deals, breadth, sector and paired instruments) return unavailable rather than creating a proxy signal.
- Calculated EMA/SMA sets, session VWAP, RSI, MACD, ATR, ADX, Bollinger Bands, stochastic, ROC, OBV, relative volume, MFI and CPR from stored candles.
- Scanner/consensus, historical model output separated from entry status, candidate details, candle chart, ₹ movement screen, screener, watchlist and source attribution.
- Backtesting with next-candle entry, ATR stop, configurable target, risk sizing, fees, slippage, trade log and summary metrics.
- Paper orders based on the latest stored candle, stop/target 1 checks during fresh candle ingestion, manual close, risk calculator and CSV trade-journal export.
- Persistent SQLite storage for candles, instruments, paper trades, watchlist, alert rules, triggered in-app notifications and source-attributed news/event records.

## Important limits

This is a functioning local research application, not a complete production trading platform. No licensed NSE/BSE data provider or credentials are supplied or connected. CSV candles are historical/imported data, never live. A provider-specific websocket adapter, exchange holiday/calendar feed, data-depth/licensing checks, and continuous live stale-feed monitor are not included. Alert rules and fresh-candle triggers work when new candles are ingested; browser/email/mobile delivery and continuous feed worker are not configured. News/events require source-attributed API ingestion. Portfolio holdings, login, multi-user separation, Postgres/Redis, production authentication/security hardening, institutional context strategy data, many long-tail indicators, robust walk-forward optimization, deployment, monitoring and backup configuration remain production work.

Backtest outputs depend on data quality and the specified fill model. The backtest uses an in-sample chronological simulation; it does not yet report a separately computed holdout result or full walk-forward analysis. A candle touching both stop and target is resolved pessimistically as stop first. Paper trading does not simulate order-book execution, partial fills, exchange rules or all taxes. No real-money orders are sent, and outputs are not investment advice or a promise of returns.

## API overview

- `GET /api/health`, `GET /api/strategies`, `GET /api/instruments`
- `POST /api/instruments`, `POST/GET /api/candles`, `GET /api/analysis`, `POST /api/evaluate`, `GET /api/scan`
- `POST /api/backtest`
- `GET/POST /api/paper/trades`, `POST /api/paper/trades/{id}/close`
- `GET/POST/DELETE /api/watchlist`, `GET/POST /api/alerts`
- `GET/POST /api/records` for source-attributed `news`, `corporate_event`, `market_calendar`, `delivery`, or `institutional_disclosure` data

The API has no internet-facing authentication. Keep it on loopback; do not expose it publicly without authentication, authorization, TLS, rate limits, auditing, and provider entitlement enforcement.
