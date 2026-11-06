# INTRADAY AI

Standalone browser application for Indian intraday market research and paper-trading workflows.

## Open

Open `index.html` in a modern browser. No build step or package install is needed. The app adapts to mobile widths. Google Fonts are loaded from a CDN when network access is available; system sans-serif/monospace fonts are the fallback.

## Included in this preview

- Dashboard with market overview, breadth, sample scanner table and connection status
- Scanner filters, sorting, screener filters, movers and watchlist controls
- Strategy catalogue and combination selection UI
- Indicator reference catalogue
- Trade setup and risk/position-size calculator
- Paper orders, sample mark-to-market, closing trades, and CSV journal export
- Backtest configuration that explicitly withholds performance results without historical data
- Assistant response boundary, alert configuration, calendar, news, corporate-event and data-source status screens
- Responsive dark interface

## Data and implementation limits

This is a front-end preview, not the complete production platform described in the brief. The visible quote and scanner sample rows are illustrative only; they are not real or delayed market data. No real NSE/BSE data provider, credentials, authentication service, backend, database, WebSocket, production monitoring, deployment, news feed, or broker is configured. Do not use the sample candidates or charts to make trades.

Paper trades and capital are stored in this browser's local storage and can be cleared with browser site data. This is not durable, account-backed persistence. The UI does not place real orders. Backtest results, live technical indicator values, and verified trade signals are intentionally unavailable until authorized data and calculation services are connected and validated.

To make the product production-capable, add a backend provider adapter for an NSE/BSE-authorized/licensed vendor, keep credentials server-side, persist instruments/candles/signals/trades in a database, implement and validate the strategy and indicator engines, and add authentication, monitoring, deployment, and integration tests. Confirm each provider's subscription and redistribution rights before exposing market data.

## Optional API foundation

The `backend/` folder contains a minimal FastAPI foundation for normalized candles. From that folder, install `requirements.txt` and run `uvicorn main:app --reload`. It stores provider-supplied candles and paper-trade records in SQLite, provides idempotent candle ingestion and basic EMA/RSI/VWAP analysis, and reports provider-disconnected until candles are ingested. This is an integration foundation, not an authorized data feed or a production-secured service. Do not expose ingestion endpoints publicly without authentication, authorization, rate limits, and provider entitlement checks.
