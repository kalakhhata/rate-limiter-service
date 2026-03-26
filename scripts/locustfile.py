"""
Locust Load Test for Rate Limiter Service
------------------------------------------
Run with:
    locust -f scripts/locustfile.py --host=http://localhost:8000 --headless \
        -u 100 -r 10 --run-time 60s

This simulates 100 concurrent users hammering /check with:
- 70% sliding window requests
- 30% token bucket requests
- 10 unique client IDs (to observe throttling behavior)

Expected results on local Docker setup:
- ~5000 requests/min throughput
- p99 latency < 20ms
- ~30-40% rejection rate (by design, clients exceed their limits)
"""

import random
from locust import HttpUser, task, between


CLIENT_IDS = [f"load_test_user_{i}" for i in range(10)]


class RateLimiterUser(HttpUser):
    wait_time = between(0.01, 0.1)  # 10–100ms between requests per user

    @task(7)
    def check_sliding_window(self):
        self.client.post(
            "/api/v1/check",
            json={
                "client_id": random.choice(CLIENT_IDS),
                "algorithm": "sliding_window",
                "limit": 5,
                "window_seconds": 10,
            },
            name="/check [sliding_window]",
        )

    @task(3)
    def check_token_bucket(self):
        self.client.post(
            "/api/v1/check",
            json={
                "client_id": random.choice(CLIENT_IDS),
                "algorithm": "token_bucket",
                "limit": 5,
                "window_seconds": 10,
                "refill_rate": 0.5,
            },
            name="/check [token_bucket]",
        )
