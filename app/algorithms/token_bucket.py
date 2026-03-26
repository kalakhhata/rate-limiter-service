"""
Token Bucket Algorithm
-----------------------
Each client gets a "bucket" with a maximum capacity (limit).
Tokens are added at a fixed refill_rate (tokens/second).
Each request consumes 1 token. If the bucket is empty → reject.

Redis storage: Hash with fields:
  - tokens: current token count (float)
  - last_refill: timestamp of last refill (float)

On each request:
1. Calculate elapsed time since last refill
2. Add (elapsed * refill_rate) tokens, capped at limit
3. If tokens >= 1 → consume 1 token, allow
4. Else → reject

Why Token Bucket?
- Allows controlled bursting up to bucket capacity
- Smooth average rate enforced by refill_rate
- Great for APIs where occasional bursts are acceptable

Trade-off vs Sliding Window:
- Allows burst traffic up to bucket size
- Lower memory: O(1) per client (just tokens + timestamp)
- Sliding window is stricter about sustained rates
"""

import time

import redis.asyncio as aioredis


async def check_token_bucket(
    redis: aioredis.Redis,
    client_id: str,
    limit: int,
    window_seconds: int,
    refill_rate: float,
) -> tuple[bool, int, float | None]:
    """
    Returns:
        allowed (bool): Whether the request is permitted
        remaining (int): Whole tokens remaining after this request
        retry_after (float | None): Seconds until next token available
    """
    now = time.time()
    key = f"rl:tb:{client_id}"

    lua_script = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local refill_rate = tonumber(ARGV[3])
    local window_seconds = tonumber(ARGV[4])

    local data = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(data[1]) or capacity
    local last_refill = tonumber(data[2]) or now

    -- Refill tokens based on elapsed time
    local elapsed = now - last_refill
    tokens = math.min(capacity, tokens + elapsed * refill_rate)

    local allowed = 0
    local remaining = 0
    local retry_after = 0

    if tokens >= 1 then
        tokens = tokens - 1
        allowed = 1
        remaining = math.floor(tokens)
    else
        -- Time until we have 1 token
        retry_after = (1 - tokens) / refill_rate
    end

    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
    redis.call('EXPIRE', key, window_seconds + 10)

    return {allowed, remaining, retry_after}
    """

    result = await redis.eval(
        lua_script,
        1,
        key,
        str(now),
        str(limit),
        str(refill_rate),
        str(window_seconds),
    )

    allowed = bool(result[0])
    remaining = int(result[1])
    retry_after = float(result[2]) if not allowed else None

    return allowed, remaining, retry_after
