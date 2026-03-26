import json

from fastapi import APIRouter, Depends, HTTPException
import redis.asyncio as aioredis

from app.algorithms.sliding_window import check_sliding_window
from app.algorithms.token_bucket import check_token_bucket
from app.core.redis_client import get_redis
from app.models.schemas import (
    AlgorithmType,
    ClientConfig,
    ClientConfigResponse,
    RateLimitRequest,
    RateLimitResponse,
    StatsResponse,
)

router = APIRouter()


async def _record_stats(redis: aioredis.Redis, client_id: str, allowed: bool):
    """Increment per-client stats counters in Redis."""
    stats_key = f"rl:stats:{client_id}"
    pipe = redis.pipeline()
    pipe.hincrby(stats_key, "total", 1)
    pipe.hincrby(stats_key, "allowed" if allowed else "rejected", 1)
    pipe.expire(stats_key, 86400 * 7)  # keep stats for 7 days
    await pipe.execute()


@router.post("/check", response_model=RateLimitResponse, tags=["Rate Limiting"])
async def check_rate_limit(
    body: RateLimitRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Check whether a request from a given client should be allowed or throttled.

    - **token_bucket**: Allows bursting; good for APIs with occasional spikes.
    - **sliding_window**: Strict per-window enforcement; no boundary bursts.
    """
    # Check if a saved config exists for this client, merge with request
    config_key = f"rl:config:{body.client_id}"
    saved = await redis.get(config_key)
    if saved:
        saved_config = ClientConfig(**json.loads(saved))
        algorithm = saved_config.algorithm
        limit = saved_config.limit
        window_seconds = saved_config.window_seconds
        refill_rate = saved_config.refill_rate
    else:
        algorithm = body.algorithm
        limit = body.limit
        window_seconds = body.window_seconds
        refill_rate = body.refill_rate

    if algorithm == AlgorithmType.sliding_window:
        allowed, remaining, retry_after = await check_sliding_window(
            redis, body.client_id, limit, window_seconds
        )
    else:
        allowed, remaining, retry_after = await check_token_bucket(
            redis, body.client_id, limit, window_seconds, refill_rate
        )

    await _record_stats(redis, body.client_id, allowed)

    return RateLimitResponse(
        allowed=allowed,
        client_id=body.client_id,
        algorithm=algorithm,
        remaining=remaining,
        limit=limit,
        window_seconds=window_seconds,
        retry_after_seconds=retry_after,
    )


@router.post("/config/{client_id}", response_model=ClientConfigResponse, tags=["Config"])
async def set_client_config(
    client_id: str,
    config: ClientConfig,
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Pre-configure rate limit rules for a specific client.
    Once set, /check will use these rules automatically for this client_id.
    """
    config.client_id = client_id
    config_key = f"rl:config:{client_id}"
    await redis.set(config_key, config.model_dump_json(), ex=86400 * 30)
    return ClientConfigResponse(message="Config saved.", config=config)


@router.get("/config/{client_id}", response_model=ClientConfig, tags=["Config"])
async def get_client_config(
    client_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Retrieve the saved config for a client."""
    config_key = f"rl:config:{client_id}"
    saved = await redis.get(config_key)
    if not saved:
        raise HTTPException(status_code=404, detail=f"No config found for client '{client_id}'")
    return ClientConfig(**json.loads(saved))


@router.delete("/config/{client_id}", tags=["Config"])
async def delete_client_config(
    client_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Delete the saved config for a client (reverts to request-level config)."""
    config_key = f"rl:config:{client_id}"
    deleted = await redis.delete(config_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No config found for client '{client_id}'")
    return {"message": f"Config for '{client_id}' deleted."}


@router.get("/stats/{client_id}", response_model=StatsResponse, tags=["Stats"])
async def get_client_stats(
    client_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Get allow/reject statistics for a specific client."""
    stats_key = f"rl:stats:{client_id}"
    data = await redis.hgetall(stats_key)
    if not data:
        raise HTTPException(status_code=404, detail=f"No stats found for client '{client_id}'")

    total = int(data.get("total", 0))
    allowed = int(data.get("allowed", 0))
    rejected = int(data.get("rejected", 0))
    rejection_rate = round((rejected / total) * 100, 2) if total else 0.0

    # Determine which algorithm was used (from config if available)
    config_key = f"rl:config:{client_id}"
    saved = await redis.get(config_key)
    algorithm = ClientConfig(**json.loads(saved)).algorithm if saved else "per-request"

    return StatsResponse(
        client_id=client_id,
        algorithm=algorithm,
        total_requests=total,
        allowed_requests=allowed,
        rejected_requests=rejected,
        rejection_rate_pct=rejection_rate,
    )


@router.delete("/stats/{client_id}", tags=["Stats"])
async def reset_client_stats(
    client_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Reset statistics for a specific client."""
    stats_key = f"rl:stats:{client_id}"
    await redis.delete(stats_key)
    return {"message": f"Stats for '{client_id}' reset."}
