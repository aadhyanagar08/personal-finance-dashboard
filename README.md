# Financial Intelligence Platform

A full-stack personal finance and investment intelligence application. It tracks bank transactions, flags anomalies, forecasts per-category spending 90 days forward, and surfaces live portfolio metrics — all driven by three independently-trainable ML models and a real-time React dashboard.

---

## ML Architecture

Three production ML models with daily automated retraining via APScheduler:

**Anomaly Detection — IsolationForest**
Flags suspicious transactions using sklearn IsolationForest (contamination=0.05). Trained on transaction amount, frequency, and category features. Retrains daily at 07:00 UTC on the full transaction history to adapt to evolving spending patterns.

**Forecasting — Meta Prophet**
Generates 90-day per-category spending forecasts with 95% confidence intervals using Meta's Prophet (cmdstanpy backend). One model per spending category, capturing seasonality and trend components. Retrains daily at 07:00 UTC.

**Auto-categorization — TF-IDF + Logistic Regression**
Classifies raw transaction descriptions into spending categories using TF-IDF vectorization + Logistic Regression with class_weight='balanced'. Runs as a FastAPI BackgroundTask on every new transaction ingestion.

All three models are persisted via joblib, versioned by training timestamp, and exposed through dedicated API endpoints for model status and manual retraining triggers.

---

## What It Does

| Feature | Detail |
|---|---|
| **Transaction tracking** | Load real bank CSV exports (tested with Scotiabank) or fall back to 2 years of deterministic synthetic data. Paginated list with filters for date range, category, and anomaly flag. |
| **Anomaly detection** | IsolationForest flags statistically unusual transactions (unexpected large charges, off-pattern merchant categories). Results are written back to the `transactions` table as `is_anomaly`. |
| **Spending forecasts** | Meta Prophet trains one model per spending category and produces 90-day forward projections with 95 % confidence bands. Forecasts are persisted and refreshable via the UI. |
| **Auto-categorization** | TF-IDF + Logistic Regression classifies uncategorized transactions in the background the moment they are created. |
| **Portfolio tracker** | Holdings with live prices via yfinance, unrealized P&L, daily change, 30/90/365-day returns, annualised volatility, and Sharpe ratio (4.5 % Bank of Canada risk-free rate). |
| **Benchmark comparison** | Normalized return series — your portfolio vs any configurable index (XIC, SPY, QQQ, ZAG.TO …) — over 30, 90, or 365 days. |
| **Stock analyzer** | Type any ticker: get a 1-year price chart, Pearson correlations with every holding, and a pro-forma analysis of how a hypothetical 5 % allocation would shift portfolio volatility and Sharpe ratio. |
| **Scheduled refresh** | APScheduler fetches fresh OHLCV data daily at 06:00 UTC and retrains ML models at 07:00 UTC — no manual intervention required. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (React 19)                       │
│  Dashboard · Transactions · Forecasts · Portfolio · Login       │
└───────────────────────┬─────────────────────────────────────────┘
                        │  HTTP  ·  Bearer JWT
┌───────────────────────▼─────────────────────────────────────────┐
│                   FastAPI  (Python 3.11+)                       │
│                                                                 │
│  /api/v1/auth          JWT register / login / me               │
│  /api/v1/transactions  CRUD · summary · anomalies              │
│  /api/v1/forecasts     per-category Prophet forecasts          │
│  /api/v1/portfolio     holdings · metrics · benchmark · analyze│
│  /api/v1/ml            trigger background categorization       │
│  /health               liveness + DB probe                     │
│                                                                 │
│  slowapi rate limiting  ·  structlog JSON logging              │
│  APScheduler AsyncIOScheduler (cron jobs)                      │
└──────────┬────────────────────────┬────────────────────────────┘
           │ async SQLAlchemy 2.x   │  BackgroundTask / scheduler
           ▼                        ▼
  ┌─────────────────┐    ┌──────────────────────────────────────┐
  │  PostgreSQL 15  │    │           ML Pipeline                │
  │                 │    │  AnomalyDetector   IsolationForest   │
  │  transactions   │◄───│  SpendingForecaster  Prophet         │
  │  forecasts      │    │  TransactionCategorizer  TF-IDF+LR  │
  │  users          │    │                                      │
  │  assets         │    │  models serialized to data/models/   │
  │  price_history  │    │  via joblib                          │
  └─────────────────┘    └──────────────────────────────────────┘
```

### Daily Scheduled Jobs

| Time (UTC) | Job | What it does |
|---|---|---|
| 06:00 | `refresh_market_data` | Downloads 2 years of OHLCV for tracked tickers via yfinance; upserts to `price_history` |
| 07:00 | `run_ml_pipeline` | Retrains anomaly detector, per-category Prophet models, and categorizer; writes predictions to DB |

---

## Tech Stack

### Backend

| Component | Technology |
|---|---|
| API framework | FastAPI 0.115 |
| ASGI server | Uvicorn + uvloop (`[standard]` extra) |
| Database | PostgreSQL 15 |
| ORM | SQLAlchemy 2.0 async (`asyncpg` driver) |
| Migrations | Alembic (Alembic uses `psycopg2` sync; app uses `asyncpg`) |
| Auth | `python-jose` HS256 JWT + `passlib` bcrypt |
| Rate limiting | slowapi (per-IP via `X-Forwarded-For`) |
| Structured logging | structlog (JSON in prod, colored console in dev) |
| Scheduling | APScheduler `AsyncIOScheduler` |
| Anomaly detection | scikit-learn `IsolationForest` |
| Time-series forecasting | Meta Prophet (cmdstanpy backend) |
| Transaction categorization | scikit-learn TF-IDF + `LogisticRegression` |
| Market data | yfinance |
| Model serialization | joblib |
| Data manipulation | pandas, numpy |
| Config | pydantic-settings (`.env` file) |

### Frontend

| Component | Technology |
|---|---|
| Framework | React 19 + TypeScript |
| Build tool | Vite 8 |
| Styling | Tailwind CSS v4 |
| Component library | shadcn/ui (Radix UI primitives) |
| Charts | Recharts |
| Routing | React Router v7 |
| Date utilities | date-fns |
| Icons | lucide-react |
| Toast notifications | Sonner |

### Infrastructure

| Component | Technology |
|---|---|
| Containerization | Docker + Docker Compose v2 |
| Backend deployment | Railway (`Procfile` + `railway.json`) |
| Frontend deployment | Vercel (`vercel.json`) |
| CI/CD | GitHub Actions + GHCR |

---

## Database Schema

```
transactions
  id             bigserial PK
  date           date       NOT NULL   (indexed)
  amount         numeric(12,2)         positive = income, negative = expense
  category       varchar(100)          (indexed)
  description    text
  source         varchar(100)          e.g. "scotiabank", "synthetic"
  is_anomaly     boolean    DEFAULT false
  created_at     timestamptz

forecasts
  id                bigserial PK
  forecast_date     date        NOT NULL   (indexed)
  predicted_amount  numeric(12,2)
  category          varchar(100)           (indexed)
  confidence_lower  numeric(12,2)
  confidence_upper  numeric(12,2)
  model_version     varchar(50)            e.g. "prophet-v1"
  created_at        timestamptz

users
  id               bigserial PK
  email            varchar(255) UNIQUE     (indexed)
  hashed_password  varchar(255)
  is_active        boolean     DEFAULT true
  created_at       timestamptz

assets
  id                      bigserial PK
  ticker                  varchar(20)  UNIQUE
  name                    varchar(255)
  asset_type              varchar(50)
  exchange                varchar(20)
  yf_symbol               varchar(20)   symbol passed to yfinance (e.g. ATZ.TO)
  quantity                numeric(16,6)
  market_price            numeric(16,4)
  market_price_currency   varchar(10)   DEFAULT 'CAD'
  book_value_cad          numeric(16,4)
  market_value_cad        numeric(16,4)
  unrealized_return_cad   numeric(16,4)
  created_at              timestamptz

price_history
  id          bigserial PK
  asset_id    bigint FK→assets(id) ON DELETE CASCADE   (indexed)
  date        date     NOT NULL                         (indexed)
  open/high/low/close  numeric(16,4)
  volume      bigint
  created_at  timestamptz
  UNIQUE (asset_id, date)
```

---

## ML Models

### 1. AnomalyDetector (`data/models/anomaly_detector.joblib`)

- **Algorithm**: `sklearn.ensemble.IsolationForest`
- **Features**: `amount` (float), `day_of_week` (0–6), `category` (label-encoded int)
- **Contamination**: 0.05 — flags ~5 % of transactions as anomalies
- **Output**: `is_anomaly` (bool) + `anomaly_score` (float; higher = more anomalous)
- **Inference**: runs in a thread-pool executor so it doesn't block the event loop

### 2. SpendingForecaster (`data/models/spending_forecaster.joblib`)

- **Algorithm**: Meta Prophet, one model per spending category
- **Training**: daily aggregated spend; missing days filled with 0 to prevent overfitting on sparse data
- **Settings**: `growth="flat"`, `weekly_seasonality=True`, `yearly_seasonality=False` (insufficient data), `changepoint_prior_scale=0.05`, `interval_width=0.95`
- **Forecast cap**: predictions clipped to 3× historical daily average to prevent exploding extrapolations
- **Horizon**: 90 days forward
- **Output**: `yhat`, `yhat_lower`, `yhat_upper` (95 % confidence interval)

### 3. TransactionCategorizer (`data/models/categorizer.joblib`)

- **Algorithm**: TF-IDF (bigrams, 10k features, sublinear TF) → `LogisticRegression` (C=1.0, `class_weight="balanced"`, max_iter=1000)
- **Input**: transaction `description` text
- **Output**: `{"category": str, "confidence": float}`
- **Background integration**: newly created transactions with `category="Uncategorized"` are auto-classified via a FastAPI `BackgroundTask`

---

## API Reference

> All endpoints except `/health` and `/api/v1/auth/register` + `/api/v1/auth/token` require `Authorization: Bearer <token>`.

### Auth — `/api/v1/auth`

| Method | Path | Description |
|---|---|---|
| `POST` | `/register` | Create account; `{ email, password }` → `UserOut` |
| `POST` | `/token` | OAuth2 password form → `{ access_token, token_type }` |
| `GET` | `/me` | Returns current user from token |

### Transactions — `/api/v1/transactions`

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Paginated list; query params: `limit`, `offset`, `date_from`, `date_to`, `category`, `is_anomaly` |
| `POST` | `/` | Create transaction; auto-categorizes in background if category omitted |
| `GET` | `/summary` | `total_income`, `total_expenses`, `net_savings`, `savings_rate` for a date range |
| `GET` | `/anomalies` | Flagged transactions only; same pagination and date filters |

### Forecasts — `/api/v1/forecasts`

| Method | Path | Description |
|---|---|---|
| `GET` | `/summary` | Projected 30-day totals by category |
| `GET` | `/{category}` | 90-day forecast with `yhat_lower`/`yhat_upper` |
| `POST` | `/refresh` | `202 Accepted`; retrains Prophet models as a background task |

### Portfolio — `/api/v1/portfolio`

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Holdings with live prices, daily change, unrealized P&L |
| `GET` | `/history` | OHLCV price history; `?days=N` (1–3650) |
| `GET` | `/metrics` | Total value, daily change, 30/90/365-day returns, annualised volatility, Sharpe ratio |
| `GET` | `/benchmark` | Normalized return series vs benchmark; `?days=N&ticker=XIC` |
| `GET` | `/analyze` | Stock analyzer — correlations + pro-forma portfolio impact; `?ticker=AAPL` |

### ML — `/api/v1/ml`

| Method | Path | Description |
|---|---|---|
| `POST` | `/categorize` | Enqueue background categorization of all `Uncategorized` transactions |

### Ops

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | `{ version, environment, db: { status }, status }` |

---

## Frontend Pages

### Dashboard
KPI cards for current-month income, expenses, net savings, and savings rate. Two charts: a horizontal bar chart of spending by category (last 6 months, top 8) and a line chart of monthly income vs expenses (last 6 months).

### Transactions
Paginated, filterable table of all transactions. Filters: date range picker, category select, anomaly-only toggle. Anomalous rows are visually distinguished.

### Forecasts
Category selector and a 90-day area+line chart showing the Prophet forecast (solid line) with a shaded 95 % confidence band. "Retrain models" button triggers `/api/v1/forecasts/refresh`.

### Portfolio
- **KPI row**: total portfolio value (CAD), unrealized P&L, daily change, 30-day return + Sharpe
- **Second metrics row**: 90-day return, 1-year return, annualised volatility
- **Benchmark chart**: normalized performance vs configurable index (default: XIC) over 30 / 90 / 365 days, with alpha displayed in the subtitle
- **Holdings table**: all positions with symbol, name, exchange, quantity, live price, daily %, market value (CAD), book cost, P&L (CAD + %)
- **Stock Analyzer**: type any ticker → 1-year price chart, correlation heatmap with all holdings (green < 0.30, amber 0.30–0.60, red > 0.60), pro-forma impact on Sharpe and volatility at 5 % allocation, and a plain-language recommendation

### Login
JWT login form; token is persisted to `localStorage` and injected into all API calls.

---

## Running Locally

### Option A — Docker Compose (recommended)

```bash
git clone https://github.com/aadhyanagar08/financial-intelligence-platform.git
cd financial-intelligence-platform

# 1. Create .env from template
cp .env.example .env
# Generate a secure SECRET_KEY and paste it in:
python -c "import secrets; print(secrets.token_hex(32))"

# 2. Build and start all three services (postgres + backend + frontend)
docker compose up --build

# Backend:  http://localhost:8000
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs
```

Alembic migrations run automatically inside the backend container before uvicorn starts (`docker-entrypoint.sh`).

### Option B — Local Python + npm

**Requirements:** Python 3.11+, Node 20+, PostgreSQL 15 running locally.

```bash
# Backend
pip install -r requirements.txt
cp .env.example .env           # edit DATABASE_URL and SECRET_KEY

alembic upgrade head           # apply migrations
uvicorn app.main:app --reload  # http://localhost:8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                    # http://localhost:3000
```

### Loading Data

**Transactions from CSV:**
```bash
# Place your bank export at data/transactions.csv
# Required columns: date, amount, category, description, source
# On next startup (or pipeline run), the file is loaded automatically.
```

**Portfolio from CSV:**
```bash
python scripts/load_portfolio.py  # loads data/portfolio.csv into assets table
```

**Run the ML pipeline manually:**
```bash
python scripts/run_ml_pipeline.py
# Steps: anomaly detection → spending forecasts → categorizer → save models
```

---

## Testing

```bash
pip install -r requirements.txt   # includes pytest, pytest-asyncio, httpx, aiosqlite

pytest                            # runs full suite (unit + integration)
pytest --cov-report=html          # HTML coverage report → htmlcov/index.html
```

Tests use an in-memory SQLite database via `aiosqlite` + `StaticPool` — no running Postgres required.

```
tests/
├── conftest.py              — async test client, auth helpers, SQLite session override
├── unit/
│   ├── test_anomaly.py      — IsolationForest: output schema, outlier detection, score ordering
│   ├── test_forecast.py     — Prophet: column schema, period count, confidence-band invariants
│   └── test_validate.py     — DataQualityChecker: all checks, chaining, report structure
└── integration/
    ├── test_auth.py         — register, login, JWT validation, expired-token rejection
    ├── test_transactions.py — CRUD, pagination, all four filters, summary math, anomaly writes
    ├── test_forecasts.py    — summary aggregation, 404 on missing category, refresh 202
    └── test_portfolio.py    — yfinance mock, empty-asset early returns, metrics schema
```

Coverage threshold: 70 % enforced by `--cov-fail-under=70` in `pyproject.toml`.

---

## Deployment

### Backend — Railway

The `Procfile` and `railway.json` at the repo root configure Railway to run:

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set these environment variables in the Railway dashboard:

```
DATABASE_URL          postgresql+asyncpg://<user>:<password>@<host>:5432/<db>
SECRET_KEY            <32-char hex>
ENVIRONMENT           prod
FRONTEND_URL          https://<your-vercel-app>.vercel.app
ACCESS_TOKEN_EXPIRE_MINUTES  30
ALPHA_VANTAGE_KEY     (optional)
```

### Frontend — Vercel

`frontend/vercel.json` configures SPA routing. Set:

```
VITE_API_URL=https://<your-railway-app>.railway.app
```

in Vercel's environment variables panel and redeploy.

---

## CI/CD

Two GitHub Actions workflows:

**`ci.yml`** — triggered on every push and pull request:
1. `lint` — `ruff check .` + `ruff format --check .`
2. `test` (needs: lint) — spins up `postgres:15-alpine`, caches `~/.cmdstan` by pyproject hash, runs `pytest --cov-report=xml`
3. `build` (needs: lint, parallel with test) — `docker build` with BuildKit layer cache; `push: false`

**`deploy.yml`** — triggered on push to `main` only:
- Calls `ci.yml` via `workflow_call` (all three jobs must pass)
- Pushes image to `ghcr.io` tagged `sha-<short>` + `latest`

---

## Key Engineering Decisions

### Async SQLAlchemy + asyncpg

FastAPI runs on a single-threaded event loop. A sync DB driver blocks the loop for the entire network round-trip — under any real concurrency that serializes requests. `asyncpg` yields control to the event loop during I/O, so hundreds of in-flight requests share the same thread without thread-pool overhead. The cost: Alembic has no async driver, so `env.py` uses a `sync_database_url` property that substitutes `psycopg2` at migration time. Two driver packages ship in the image — accepted trade-off in the SQLAlchemy ecosystem.

### Prophet over ARIMA for spending forecasts

ARIMA requires stationarity, manual (p, d, q) selection per category, and breaks on irregularly-spaced data (a weekend with no Food spend is a structural gap, not a zero). That would mean a separate preprocessing and hyperparameter search step per category. Prophet treats trend + weekly seasonality as additive components, handles missing dates natively, and produces calibrated 95 % confidence intervals via its Stan backend — at the cost of a ~200 MB CmdStan binary in the Docker image and a slower cold start. For a daily background job, that trade-off is acceptable.

### Stateless JWT over server-side sessions

Server-side sessions require a shared store every replica can read; without it, a sticky load balancer is mandatory and any pod restart invalidates all sessions. JWT validation is a local operation — verify signature, check `exp`, done — with zero inter-process communication. Revocation requires an out-of-band denylist (not yet implemented); short-lived tokens (30 min default) bound the exposure window.

### IsolationForest for unsupervised anomaly detection

Supervised detection needs labeled anomaly examples — which don't exist at bootstrap. IsolationForest requires no labels: it isolates points that are easy to separate from the bulk of the distribution using random partitioning trees, O(n log n), small memory. The three features (amount, day_of_week, category) capture the most common anomaly patterns. `contamination=0.05` is a global threshold; per-category contamination rates would be a natural next step for categories with inherently high variance (e.g. Housing).

### Pro-forma portfolio impact at 5 % allocation

The stock analyzer simulates adding the target ticker at a 5 % weight blended with 95 % of the existing portfolio returns. This gives a concrete, comparable signal — Sharpe delta and volatility delta — without requiring the user to specify a dollar amount. The 5 % convention mirrors the position-sizing rule of thumb used in factor investing literature.
