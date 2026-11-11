# INTRADAY AI

Indian-market research API and paper-trading foundation. This repository currently deploys its FastAPI service on Vercel; the backend domain is not the trading dashboard.

## Vercel

Vercel detects the FastAPI app exported as `app` in root `main.py` and installs dependencies from `requirements.txt`.

After deployment:

- `/` and `/api` show the backend status page.
- `/docs` shows the interactive API documentation.
- `/health` and `/api/health` return service and storage status.
- Candle ingestion: `POST /v1/ingest/candles`
- Analysis: `GET /v1/analysis/{exchange}/{symbol}?interval=5m`
- Candles: `GET /v1/candles/{exchange}/{symbol}?interval=5m`
- Paper records: `POST /v1/paper/orders`, `GET /v1/paper/trades`

For a separately deployed browser frontend, configure Vercel environment variable `FRONTEND_ORIGINS` as a comma-separated allowlist of exact origins, for example `https://your-frontend.example`. Leave it unset for same-origin calls. CORS does not use wildcard origins or credentials.

## Persistence warning

Vercel uses `/tmp` for the SQLite fallback. That storage is ephemeral and can be cleared between function instances or deployments. The health response reports this. Do not use it as durable production trade, signal, or candle storage. Configure a managed PostgreSQL database before relying on persistence.

## Local development

Requires Python 3.10+:

```powershell
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/docs`.

## Accuracy-first decision gate

The Node dashboard exposes `POST /api/accuracy/assessment` with a JSON body such as:

```json
{
  "exchange": "NSE",
  "symbol": "TATASTEEL",
  "interval": "5m",
  "capital": 1000,
  "estimatedRoundTripCosts": null
}
```

The example cost is only a schema illustration, not a Groww fee quote. Supply current, verified round-trip charges; do not assume zero. The gate fails closed unless candle count and freshness, positive-volume history, three independent strategy families, higher-timeframe trend, index trend, sector trend, risk sizing, and net reward/risk checks all pass. Capital defaults to ₹1,000 and risk per trade is capped at 0.5% in this gate. With provider/context feeds not connected, the correct result is `NO TRADE`.

The Risk Management screen also defaults to ₹1,000 capital. It requires a target and total round-trip charges, enforces a maximum 1% risk input, and rejects a setup if quantity, capital, or net 1:2 reward/risk checks fail. A passing sizing calculation is not a trading signal.

## Local dashboard

The browser dashboard is served by the Node 22+ application:

```bash
npm start
```

Open `http://127.0.0.1:3000`. The local dashboard stores its SQLite database under `data/`. Back up this folder; it is not a multi-user or cloud-durable production database. The separate root `main.py` FastAPI service remains an API foundation and does not serve the browser dashboard.

## Data and trading boundaries

No market-data provider is connected. Only ingest market data you are licensed to store and use; every candle must include a timezone and source label. This API does not fetch live data, issue verified live signals, or submit real-money orders. It is a research and paper-trading foundation, not investment advice. Authentication, account isolation, rate limits, audit logging, durable database configuration, provider integration and production monitoring must be completed before public use.
