"""
Tests for Rate Limiter Service
Run: pytest tests/ -v
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ─── Sliding Window Tests ────────────────────────────────────────────────────

class TestSlidingWindow:
    """Tests for sliding window algorithm logic."""

    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self):
        """Should allow requests when under the limit."""
        from app.algorithms.sliding_window import check_sliding_window

        mock_redis = AsyncMock()
        # Simulate: 3 requests used out of 10 limit, no oldest entry needed
        mock_redis.eval = AsyncMock(return_value=[1, 6, 0])

        allowed, remaining, retry_after = await check_sliding_window(
            mock_redis, "user_1", limit=10, window_seconds=60
        )

        assert allowed is True
        assert remaining == 6
        assert retry_after is None

    @pytest.mark.asyncio
    async def test_rejects_requests_over_limit(self):
        """Should reject requests when at the limit."""
        from app.algorithms.sliding_window import check_sliding_window

        mock_redis = AsyncMock()
        # Simulate: limit hit, retry in 30 seconds
        mock_redis.eval = AsyncMock(return_value=[0, 0, 30.0])

        allowed, remaining, retry_after = await check_sliding_window(
            mock_redis, "user_1", limit=10, window_seconds=60
        )

        assert allowed is False
        assert remaining == 0
        assert retry_after == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_remaining_decrements_correctly(self):
        """Remaining should equal limit - requests_made - 1 after this request."""
        from app.algorithms.sliding_window import check_sliding_window

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[1, 4, 0])

        _, remaining, _ = await check_sliding_window(
            mock_redis, "user_2", limit=10, window_seconds=60
        )
        assert remaining == 4


# ─── Token Bucket Tests ──────────────────────────────────────────────────────

class TestTokenBucket:
    """Tests for token bucket algorithm logic."""

    @pytest.mark.asyncio
    async def test_allows_when_tokens_available(self):
        """Should allow request when bucket has tokens."""
        from app.algorithms.token_bucket import check_token_bucket

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[1, 9, 0])

        allowed, remaining, retry_after = await check_token_bucket(
            mock_redis, "user_1", limit=10, window_seconds=60, refill_rate=1.0
        )

        assert allowed is True
        assert remaining == 9
        assert retry_after is None

    @pytest.mark.asyncio
    async def test_rejects_when_bucket_empty(self):
        """Should reject when bucket is empty."""
        from app.algorithms.token_bucket import check_token_bucket

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[0, 0, 0.5])

        allowed, remaining, retry_after = await check_token_bucket(
            mock_redis, "user_1", limit=10, window_seconds=60, refill_rate=2.0
        )

        assert allowed is False
        assert remaining == 0
        assert retry_after == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_retry_after_reflects_refill_rate(self):
        """Retry after should be shorter with higher refill rate."""
        from app.algorithms.token_bucket import check_token_bucket

        mock_redis_slow = AsyncMock()
        mock_redis_slow.eval = AsyncMock(return_value=[0, 0, 1.0])

        mock_redis_fast = AsyncMock()
        mock_redis_fast.eval = AsyncMock(return_value=[0, 0, 0.1])

        _, _, retry_slow = await check_token_bucket(
            mock_redis_slow, "user_slow", limit=5, window_seconds=60, refill_rate=1.0
        )
        _, _, retry_fast = await check_token_bucket(
            mock_redis_fast, "user_fast", limit=5, window_seconds=60, refill_rate=10.0
        )

        assert retry_slow > retry_fast


# ─── API Route Tests ─────────────────────────────────────────────────────────

class TestAPIRoutes:
    """Integration-style tests for the FastAPI routes."""

    @pytest.mark.asyncio
    async def test_check_endpoint_returns_allowed(self):
        """POST /check should return allowed=True under limit."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.redis_client import get_redis

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.eval = AsyncMock(return_value=[1, 9, 0])
        mock_redis.pipeline = AsyncMock()
        pipeline_mock = AsyncMock()
        pipeline_mock.hincrby = AsyncMock()
        pipeline_mock.expire = AsyncMock()
        pipeline_mock.execute = AsyncMock(return_value=[1, 1, 1])
        mock_redis.pipeline.return_value = pipeline_mock

        app.dependency_overrides[get_redis] = lambda: mock_redis

        with TestClient(app) as client:
            response = client.post("/api/v1/check", json={
                "client_id": "test_user",
                "algorithm": "sliding_window",
                "limit": 10,
                "window_seconds": 60,
            })

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["client_id"] == "test_user"
        assert "remaining" in data

    @pytest.mark.asyncio
    async def test_check_endpoint_returns_throttled(self):
        """POST /check should return allowed=False when throttled."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.redis_client import get_redis

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.eval = AsyncMock(return_value=[0, 0, 15.0])
        pipeline_mock = AsyncMock()
        pipeline_mock.hincrby = AsyncMock()
        pipeline_mock.expire = AsyncMock()
        pipeline_mock.execute = AsyncMock(return_value=[1, 1, 1])
        mock_redis.pipeline.return_value = pipeline_mock

        app.dependency_overrides[get_redis] = lambda: mock_redis

        with TestClient(app) as client:
            response = client.post("/api/v1/check", json={
                "client_id": "throttled_user",
                "algorithm": "sliding_window",
                "limit": 5,
                "window_seconds": 60,
            })

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is False
        assert data["retry_after_seconds"] == pytest.approx(15.0)

    def test_health_check(self):
        """GET /health should return healthy status."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.redis_client import redis_client

        with patch.object(redis_client, 'ping', new_callable=AsyncMock, return_value=True):
            with TestClient(app) as client:
                response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
