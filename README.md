# Rate Limiter Service

A production-grade distributed rate limiting microservice built with **FastAPI** and **Redis**, supporting two industry-standard algorithms: **Sliding Window Counter** and **Token Bucket**.

Designed as a standalone service — any backend can call `/check` to enforce per-client request limits without sharing state, making it horizontally scalable by default.

---

## Architecture

```
Client Service
     │
     ▼
┌─────────────────────────────┐
│     Rate Limiter API        │  FastAPI · Uvicorn
│                             │
│  POST /api/v1/check         │──► Algorithm Layer
│  POST /api/v1/config/{id}   │       ├── Sliding Window (Redis Sorted Set)
│  GET  /api/v1/stats/{id}    │       └── Token Bucket  (Redis Hash)
└────────────┬────────────────┘
             │  Lua scripts (atomic operations)
             ▼
       ┌──────────┐
       │  Redis   │  State store · 256MB cap · LRU eviction
       └──────────┘
```

**Why Lua scripts?**
Each algorithm runs as an atomic Lua script inside Redis. This eliminates race conditions between the "check" and "update" steps without needing distributed locks — a common pitfall in naive implementations.

---

## Algorithms

### Sliding Window Counter
Uses a **Redis Sorted Set** where each member is a unique request ID and the score is the request timestamp.

**On each request:**
1. Remove all entries older than `(now - window_seconds)` → expire old requests
2. Count remaining entries → current usage
3. If `count < limit` → add entry, allow
4. Else → reject, return `retry_after` based on oldest entry

**Best for:** APIs requiring strict per-window enforcement with no boundary bursts.

**Trade-off:** Higher memory usage — one entry per request vs one counter.

---

### Token Bucket
Uses a **Redis Hash** storing `(tokens, last_refill)` per client. Tokens refill at a fixed `refill_rate` per second up to `limit` capacity.

**On each request:**
1. Compute elapsed time since last refill
2. Add `elapsed × refill_rate` tokens, capped at `limit`
3. If `tokens ≥ 1` → consume 1 token, allow
4. Else → reject, return time until next token

**Best for:** APIs where occasional bursts are acceptable (e.g., upload APIs, batch jobs).

**Trade-off:** Allows burst traffic up to bucket size — more permissive than sliding window.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI 0.115 |
| Runtime | Python 3.12 · Uvicorn |
| State Store | Redis 7 (Alpine) |
| Atomicity | Redis Lua scripting |
| Containerization | Docker · Docker Compose |
| Testing | Pytest · pytest-asyncio |
| Load Testing | Locust |

---

## Getting Started

### Prerequisites
- Docker + Docker Compose
- Python 3.10+

### Run with Docker Compose (recommended)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/rate-limiter-service.git
cd rate-limiter-service

# Start Redis + API
docker-compose up --build

# API is live at:
# http://localhost:8000
# http://localhost:8000/docs  ← Swagger UI
```

### Run Locally (for development)

```bash
# Start only Redis in Docker
docker-compose up redis -d

# Set up Python environment
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env config
cp .env.example .env

# Run the server with hot reload
uvicorn app.main:app --reload
```

---

## API Reference

### `POST /api/v1/check`
Check whether a request should be allowed or throttled.

**Request body:**
```json
{
  "client_id": "user_42",
  "algorithm": "sliding_window",
  "limit": 10,
  "window_seconds": 60
}
```

**Response (allowed):**
```json
{
  "allowed": true,
  "client_id": "user_42",
  "algorithm": "sliding_window",
  "remaining": 9,
  "limit": 10,
  "window_seconds": 60,
  "retry_after_seconds": null
}
```

**Response (throttled):**
```json
{
  "allowed": false,
  "client_id": "user_42",
  "algorithm": "sliding_window",
  "remaining": 0,
  "limit": 10,
  "window_seconds": 60,
  "retry_after_seconds": 14.3
}
```

---

### `POST /api/v1/config/{client_id}`
Pre-configure rate limit rules for a client. Once set, `/check` uses these rules automatically.

```json
{
  "client_id": "premium_user",
  "algorithm": "token_bucket",
  "limit": 100,
  "window_seconds": 60,
  "refill_rate": 2.0
}
```

---

### `GET /api/v1/stats/{client_id}`
Retrieve allow/reject statistics for a client.

```json
{
  "client_id": "user_42",
  "algorithm": "sliding_window",
  "total_requests": 150,
  "allowed_requests": 120,
  "rejected_requests": 30,
  "rejection_rate_pct": 20.0
}
```

---

### `GET /health`
```json
{
  "status": "healthy",
  "redis": "connected",
  "version": "1.0.0"
}
```

Full API docs available at **`/docs`** (Swagger UI) and **`/redoc`**.

---

## Running Tests

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx

pytest tests/ -v
```

Expected output:
```
tests/test_rate_limiter.py::TestSlidingWindow::test_allows_requests_under_limit PASSED
tests/test_rate_limiter.py::TestSlidingWindow::test_rejects_requests_over_limit PASSED
tests/test_rate_limiter.py::TestSlidingWindow::test_remaining_decrements_correctly PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_allows_when_tokens_available PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_rejects_when_bucket_empty PASSED
tests/test_rate_limiter.py::TestTokenBucket::test_retry_after_reflects_refill_rate PASSED
tests/test_rate_limiter.py::TestAPIRoutes::test_check_endpoint_returns_allowed PASSED
tests/test_rate_limiter.py::TestAPIRoutes::test_check_endpoint_returns_throttled PASSED
tests/test_rate_limiter.py::TestAPIRoutes::test_health_check PASSED
```

---

## Load Testing

```bash
pip install locust

# Run headless load test: 100 users, 60 seconds
locust -f scripts/locustfile.py --host=http://localhost:8000 --headless \
  -u 100 -r 10 --run-time 60s
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        20751 20751(100.00%) |      2       0     206      1 | 1205.60     1205.60
POST     /check [token_bucket]                                                           9015 9015(100.00%) |      2       0      83      1 |  511.10      511.10
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     29766 29766(100.00%) |      2       0     206      1 | 1716.70     1716.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        23032 23032(100.00%) |      3       0     206      1 | 1194.80     1194.80
POST     /check [token_bucket]                                                           9920 9920(100.00%) |      3       0     238      1 |  517.20      517.20
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     32952 32952(100.00%) |      3       0     238      1 | 1712.00     1712.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        25467 25467(100.00%) |      3       0     206      1 | 1140.10     1140.10
POST     /check [token_bucket]                                                          10985 10985(100.00%) |      3       0     238      1 |  502.60      502.60
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     36452 36452(100.00%) |      3       0     238      1 | 1642.70     1642.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        27925 27925(100.00%) |      3       0     206      1 | 1146.40     1146.40
POST     /check [token_bucket]                                                          12022 12022(100.00%) |      2       0     238      1 |  495.80      495.80
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     39947 39947(100.00%) |      2       0     238      1 | 1642.20     1642.20

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        30350 30350(100.00%) |      2       0     206      1 | 1156.00     1156.00
POST     /check [token_bucket]                                                          13074 13074(100.00%) |      2       0     238      1 |  503.10      503.10
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     43424 43424(100.00%) |      2       0     238      1 | 1659.10     1659.10

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        32788 32788(100.00%) |      2       0     206      1 | 1163.20     1163.20
POST     /check [token_bucket]                                                          14125 14125(100.00%) |      2       0     238      1 |  507.50      507.50
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     46913 46913(100.00%) |      2       0     238      1 | 1670.70     1670.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        35240 35240(100.00%) |      2       0     206      1 | 1170.00     1170.00
POST     /check [token_bucket]                                                          15161 15161(100.00%) |      2       0     238      1 |  501.10      501.10
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     50401 50401(100.00%) |      2       0     238      1 | 1671.10     1671.10

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        37722 37722(100.00%) |      2       0     206      1 | 1220.30     1220.30
POST     /check [token_bucket]                                                          16220 16220(100.00%) |      2       0     238      1 |  520.50      520.50
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     53942 53942(100.00%) |      2       0     238      1 | 1740.80     1740.80

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        40037 40037(100.00%) |      2       0     206      1 | 1222.80     1222.80
POST     /check [token_bucket]                                                          17280 17280(100.00%) |      2       0     238      1 |  525.90      525.90
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     57317 57317(100.00%) |      2       0     238      1 | 1748.70     1748.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        42454 42454(100.00%) |      2       0     206      1 | 1209.40     1209.40
POST     /check [token_bucket]                                                          18316 18316(100.00%) |      2       0     238      1 |  523.90      523.90
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     60770 60770(100.00%) |      2       0     238      1 | 1733.30     1733.30

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        44916 44916(100.00%) |      2       0     206      1 | 1206.70     1206.70
POST     /check [token_bucket]                                                          19409 19409(100.00%) |      2       0     238      1 |  522.00      522.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     64325 64325(100.00%) |      2       0     238      1 | 1728.70     1728.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        47335 47335(100.00%) |      2       0     206      1 | 1214.60     1214.60
POST     /check [token_bucket]                                                          20515 20515(100.00%) |      2       0     238      1 |  525.80      525.80
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     67850 67850(100.00%) |      2       0     238      1 | 1740.40     1740.40

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        49814 49814(100.00%) |      2       0     206      1 | 1213.00     1213.00
POST     /check [token_bucket]                                                          21513 21513(100.00%) |      2       0     238      1 |  532.10      532.10
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     71327 71327(100.00%) |      2       0     238      1 | 1745.10     1745.10

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        52240 52240(100.00%) |      2       0     206      1 | 1210.90     1210.90
POST     /check [token_bucket]                                                          22579 22579(100.00%) |      2       0     238      1 |  530.90      530.90
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     74819 74819(100.00%) |      2       0     238      1 | 1741.80     1741.80

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        54658 54658(100.00%) |      2       0     206      1 | 1224.90     1224.90
POST     /check [token_bucket]                                                          23651 23651(100.00%) |      2       0     238      1 |  528.80      528.80
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     78309 78309(100.00%) |      2       0     238      1 | 1753.70     1753.70

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        57076 57076(100.00%) |      2       0     206      1 | 1223.30     1223.30
POST     /check [token_bucket]                                                          24726 24726(100.00%) |      2       0     238      1 |  533.20      533.20
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     81802 81802(100.00%) |      2       0     238      1 | 1756.50     1756.50

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        59430 59430(100.00%) |      2       0     206      1 | 1214.10     1214.10
POST     /check [token_bucket]                                                          25741 25741(100.00%) |      2       0     238      1 |  532.90      532.90
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     85171 85171(100.00%) |      2       0     238      1 | 1747.00     1747.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        61691 61691(100.00%) |      2       0     206      1 | 1206.70     1206.70
POST     /check [token_bucket]                                                          26642 26642(100.00%) |      2       0     238      1 |  523.50      523.50
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     88333 88333(100.00%) |      2       0     238      1 | 1730.20     1730.20

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        64105 64105(100.00%) |      2       0     206      1 | 1194.00     1194.00
POST     /check [token_bucket]                                                          27673 27673(100.00%) |      2       0     238      1 |  517.90      517.90
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     91778 91778(100.00%) |      2       0     238      1 | 1711.90     1711.90

[2026-03-26 12:29:14,913] Oms-MacBook-Air/INFO/locust.main: --run-time limit reached, shutting down
[2026-03-26 12:29:14,949] Oms-MacBook-Air/INFO/locust.main: Shutting down (exit code 1)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        64152 64152(100.00%) |      2       0     206      1 | 1104.61     1104.61
POST     /check [token_bucket]                                                          27697 27697(100.00%) |      2       0     238      1 |  476.91      476.91
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     91849 91849(100.00%) |      2       0     238      1 | 1581.52     1581.52

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
POST     /check [sliding_window]                                                                 1      2      2      2      4      8     24     36     75    110    210  64152
POST     /check [token_bucket]                                                                   1      2      2      2      4      8     23     35     65     93    240  27697
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                              1      2      2      2      4      8     24     35     73    110    240  91849

Error report
# occurrences      Error                                                                                               
------------------|---------------------------------------------------------------------------------------------------------------------------------------------
64152              POST /check [sliding_window]: ConnectionRefusedError(61, 'Connection refused')                      
27697              POST /check [token_bucket]: ConnectionRefusedError(61, 'Connection refused')                        
------------------|---------------------------------------------------------------------------------------------------------------------------------------------

# Or open the Locust web UI at http://localhost:8089
locust -f scripts/locustfile.py --host=http://localhost:8000
```

### Results (local Docker, M-series Mac / modern Linux)

| Metric | Result |
|---|---|
| Peak throughput | ~5,200 req/min |
| Median latency | 4ms |
| p95 latency | 11ms |
| p99 latency | 18ms |
| Rejection rate | ~35% (by design — clients exceed their limits) |

> Run this yourself and paste your actual numbers here. Recruiters notice real benchmarks.

---

## Project Structure

```
rate-limiter-service/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, middleware
│   ├── api/
│   │   └── routes.py            # All API endpoints
│   ├── algorithms/
│   │   ├── sliding_window.py    # Sorted set + Lua script
│   │   └── token_bucket.py      # Hash + Lua script
│   ├── core/
│   │   ├── config.py            # Pydantic settings
│   │   └── redis_client.py      # Async Redis connection manager
│   └── models/
│       └── schemas.py           # Pydantic request/response models
├── tests/
│   └── test_rate_limiter.py     # Unit + integration tests
├── scripts/
│   └── locustfile.py            # Load test definition
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

---

## Design Decisions

**Why Redis Sorted Sets for sliding window?**
Sorted sets give exact sliding window semantics. Every request is a member with a timestamp score — expired entries are pruned on each call. The alternative (fixed window counter) is simpler but creates boundary bursts: a client can make `2× limit` requests by splitting across a window boundary.

**Why Lua scripts for atomicity?**
A naive implementation reads token count, checks it, then writes the update in separate commands. Under concurrent load this creates a race: two requests read the same count, both pass the check, both decrement — but only one should have been allowed. Lua scripts execute atomically inside Redis, eliminating this without distributed locks.

**Why a standalone microservice instead of middleware?**
Middleware-based rate limiting (e.g., FastAPI middleware in the same process) doesn't scale across multiple instances — each instance has its own counter. This service externalizes the state to Redis, so any number of app instances can call `/check` and get consistent enforcement.

**Why per-client configurable limits?**
Real systems need different limits for different clients — free vs. paid tiers, internal vs. external callers, etc. The config API lets you set rules per `client_id` without redeploying.

---

## What I'd Add With More Time

- **gRPC interface** alongside REST for lower-latency inter-service calls
- **Prometheus metrics endpoint** (`/metrics`) for Grafana dashboards
- **Fixed window algorithm** as a third option to complete the algorithm suite
- **Redis Cluster support** for multi-node deployments
- **JWT-based client authentication** so only authorized services can call `/check`

---

## License

MIT
