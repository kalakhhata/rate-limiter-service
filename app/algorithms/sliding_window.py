"""
Sliding Window Counter Algorithm
---------------------------------
Uses a Redis Sorted Set where each member is a unique request ID
and the score is the request timestamp (epoch seconds).

On each request:
1. Remove all entries older than (now - window_seconds)  → clean expired
2. Count remaining entries                               → current usage
3. If count < limit → add new entry, allow request
4. Else → reject request, return retry_after

Why Sorted Set?
- O(log N) insert
- O(log N + M) range delete  (M = expired entries removed)
- Gives exact sliding window semantics, not approximate
- Stateless app layer → horizontally scalable

Trade-off vs Fixed Window:
- Uses more memory per client (one entry per request vs one counter)
- But eliminates the "boundary burst" problem of fixed windows
"""

import time
import uuid

import redis.asyncio as aioredis


async def check_sliding_window(
    redis: aioredis.Redis,
    client_id: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int, float | None]:
    """
    Returns:
        allowed (bool): Whether the request is permitted
        remaining (int): Requests remaining in the current window
        retry_after (float | None): Seconds to wait before retrying, or None if allowed
    """
    now = time.time()
    window_start = now - window_seconds
    key = f"rl:sw:{client_id}"

    # Lua script for atomicity — avoids race conditions between check and set
    lua_script = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window_start = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local window_seconds = tonumber(ARGV[4])
    local member = ARGV[5]

    -- Remove expired entries
    redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

    -- Count current requests in window
    local count = redis.call('ZCARD', key)

    if count < limit then
        -- Allow: add this request
        redis.call('ZADD', key, now, member)
        redis.call('EXPIRE', key, window_seconds + 1)
        return {1, limit - count - 1, 0}
    else
        -- Reject: find oldest entry to compute retry_after
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local oldest_score = tonumber(oldest[2])
        local retry_after = (oldest_score + window_seconds) - now
        return {0, 0, retry_after}
    end
    """

    result = await redis.eval(
        lua_script,
        1,
        key,
        str(now),
        str(window_start),
        str(limit),
        str(window_seconds),
        str(uuid.uuid4()),
    )

    allowed = bool(result[0])
    remaining = int(result[1])
    retry_after = float(result[2]) if not allowed else None

    return allowed, remaining, retry_after
