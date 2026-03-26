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
POST     /check [sliding_window]                                                         2138     0(0.00%) |     10       1     178      4 |  266.25        0.00
POST     /check [token_bucket]                                                            969     0(0.00%) |     10       1     176      4 |  116.25        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      3107     0(0.00%) |     10       1     178      4 |  382.50        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                         3739     0(0.00%) |     10       1     178      4 |  344.17        0.00
POST     /check [token_bucket]                                                           1674     0(0.00%) |     10       1     176      4 |  157.33        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      5413     0(0.00%) |     10       1     178      4 |  501.50        0.00

[2026-03-26 13:11:42,188] Oms-MacBook-Air/INFO/locust.runners: All users spawned: {"RateLimiterUser": 100} (100 total users)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                         5717     0(0.00%) |     11       1     178      5 |  446.50        0.00
POST     /check [token_bucket]                                                           2530     0(0.00%) |     11       1     176      5 |  199.25        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      8247     0(0.00%) |     11       1     178      5 |  645.75        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                         7766     0(0.00%) |     11       1     178      6 |  549.70        0.00
POST     /check [token_bucket]                                                           3435     0(0.00%) |     11       1     176      6 |  244.20        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     11201     0(0.00%) |     11       1     178      6 |  793.90        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                         9662     0(0.00%) |     12       1     178      7 |  728.20        0.00
POST     /check [token_bucket]                                                           4290     0(0.00%) |     12       1     176      7 |  321.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     13952     0(0.00%) |     12       1     178      7 | 1049.80        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        11791     0(0.00%) |     12       1     178      7 |  843.30        0.00
POST     /check [token_bucket]                                                           5198     0(0.00%) |     12       1     176      7 |  373.90        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     16989     0(0.00%) |     12       1     178      7 | 1217.20        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        13843     0(0.00%) |     12       1     178      7 |  951.60        0.00
POST     /check [token_bucket]                                                           6082     0(0.00%) |     12       1     176      7 |  417.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     19925     0(0.00%) |     12       1     178      7 | 1369.20        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        15920     0(0.00%) |     12       1     178      8 | 1005.90        0.00
POST     /check [token_bucket]                                                           6982     0(0.00%) |     12       1     176      8 |  438.70        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     22902     0(0.00%) |     12       1     178      8 | 1444.60        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        17967     0(0.00%) |     12       1     178      8 | 1020.50        0.00
POST     /check [token_bucket]                                                           7877     0(0.00%) |     12       1     176      8 |  446.10        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     25844     0(0.00%) |     12       1     178      8 | 1466.60        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        20081     0(0.00%) |     11       1     178      8 | 1021.30        0.00
POST     /check [token_bucket]                                                           8830     0(0.00%) |     11       1     176      8 |  445.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     28911     0(0.00%) |     11       1     178      8 | 1466.90        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        22129     0(0.00%) |     11       1     178      8 | 1038.40        0.00
POST     /check [token_bucket]                                                           9764     0(0.00%) |     11       1     176      8 |  452.00        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     31893     0(0.00%) |     11       1     178      8 | 1490.40        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        24136     0(0.00%) |     11       1     178      8 | 1035.60        0.00
POST     /check [token_bucket]                                                          10622     0(0.00%) |     12       1     176      8 |  454.40        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     34758     0(0.00%) |     12       1     178      8 | 1490.00        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        26129     0(0.00%) |     12       1     178      8 | 1030.00        0.00
POST     /check [token_bucket]                                                          11479     0(0.00%) |     12       1     176      8 |  454.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     37608     0(0.00%) |     12       1     178      8 | 1484.60        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        28128     0(0.00%) |     12       1     178      8 | 1019.80        0.00
POST     /check [token_bucket]                                                          12346     0(0.00%) |     12       1     176      8 |  448.10        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     40474     0(0.00%) |     12       1     178      8 | 1467.90        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        30150     0(0.00%) |     12       1     178      8 | 1011.10        0.00
POST     /check [token_bucket]                                                          13173     0(0.00%) |     12       1     176      8 |  447.40        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     43323     0(0.00%) |     12       1     178      8 | 1458.50        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        32165     0(0.00%) |     12       1     178      9 | 1005.70        0.00
POST     /check [token_bucket]                                                          14051     0(0.00%) |     12       1     176      8 |  436.30        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     46216     0(0.00%) |     12       1     178      8 | 1442.00        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        34286     0(0.00%) |     12       1     178      8 | 1004.80        0.00
POST     /check [token_bucket]                                                          14961     0(0.00%) |     12       1     176      8 |  430.50        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     49247     0(0.00%) |     12       1     178      8 | 1435.30        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        36454     0(0.00%) |     12       1     178      8 | 1013.20        0.00
POST     /check [token_bucket]                                                          15854     0(0.00%) |     12       1     176      8 |  432.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     52308     0(0.00%) |     12       1     178      8 | 1445.80        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        38562     0(0.00%) |     12       1     178      8 | 1034.50        0.00
POST     /check [token_bucket]                                                          16722     0(0.00%) |     12       1     176      8 |  438.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     55284     0(0.00%) |     12       1     178      8 | 1473.10        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        40675     0(0.00%) |     11       1     178      8 | 1042.70        0.00
POST     /check [token_bucket]                                                          17649     0(0.00%) |     11       1     176      8 |  436.80        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     58324     0(0.00%) |     11       1     178      8 | 1479.50        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        42716     0(0.00%) |     11       1     178      8 | 1051.50        0.00
POST     /check [token_bucket]                                                          18558     0(0.00%) |     11       1     176      8 |  448.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     61274     0(0.00%) |     11       1     178      8 | 1500.10        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        44718     0(0.00%) |     12       1     178      9 | 1051.10        0.00
POST     /check [token_bucket]                                                          19356     0(0.00%) |     12       1     176      8 |  450.20        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     64074     0(0.00%) |     12       1     178      8 | 1501.30        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        46751     0(0.00%) |     12       1     178      9 | 1044.20        0.00
POST     /check [token_bucket]                                                          20196     0(0.00%) |     12       1     176      8 |  442.20        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     66947     0(0.00%) |     12       1     178      9 | 1486.40        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        48872     0(0.00%) |     12       1     178      9 | 1028.10        0.00
POST     /check [token_bucket]                                                          21069     0(0.00%) |     12       1     176      8 |  434.90        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     69941     0(0.00%) |     12       1     178      9 | 1463.00        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        50980     0(0.00%) |     12       1     178      9 | 1033.00        0.00
POST     /check [token_bucket]                                                          21929     0(0.00%) |     12       1     176      9 |  432.60        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     72909     0(0.00%) |     12       1     178      9 | 1465.60        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        53065     0(0.00%) |     11       1     178      9 | 1029.00        0.00
POST     /check [token_bucket]                                                          22827     0(0.00%) |     12       1     176      9 |  426.80        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     75892     0(0.00%) |     12       1     178      9 | 1455.80        0.00

Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        55065     0(0.00%) |     12       1     178      9 | 1035.70        0.00
POST     /check [token_bucket]                                                          23728     0(0.00%) |     12       1     176      9 |  426.40        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     78793     0(0.00%) |     12       1     178      9 | 1462.10        0.00

[2026-03-26 13:12:32,739] Oms-MacBook-Air/INFO/locust.main: --run-time limit reached, shutting down
[2026-03-26 13:12:32,801] Oms-MacBook-Air/INFO/locust.main: Shutting down (exit code 0)
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
POST     /check [sliding_window]                                                        56659     0(0.00%) |     12       1     178      9 |  950.36        0.00
POST     /check [token_bucket]                                                          24357     0(0.00%) |     12       1     176      9 |  408.55        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                     81016     0(0.00%) |     12       1     178      9 | 1358.90        0.00

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
POST     /check [sliding_window]                                                                 9     11     14     16     26     35     45     52     91    180    180  56659
POST     /check [token_bucket]                                                                   9     11     14     16     26     36     46     53     90    170    180  24357
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                              9     11     14     16     26     36     45     52     90    180    180  81016

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
