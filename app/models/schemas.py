from enum import Enum
from pydantic import BaseModel, Field


class AlgorithmType(str, Enum):
    token_bucket = "token_bucket"
    sliding_window = "sliding_window"


class RateLimitRequest(BaseModel):
    client_id: str = Field(..., description="Unique identifier for the client (e.g. user ID, IP, API key)")
    algorithm: AlgorithmType = Field(AlgorithmType.sliding_window, description="Rate limiting algorithm to use")
    limit: int = Field(10, ge=1, le=10000, description="Max number of requests allowed in the window")
    window_seconds: int = Field(60, ge=1, le=86400, description="Time window in seconds")

    # Token bucket specific
    refill_rate: float = Field(1.0, ge=0.01, description="Tokens added per second (token bucket only)")

    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "user_42",
                "algorithm": "sliding_window",
                "limit": 10,
                "window_seconds": 60,
            }
        }


class RateLimitResponse(BaseModel):
    allowed: bool
    client_id: str
    algorithm: str
    remaining: int
    limit: int
    window_seconds: int
    retry_after_seconds: float | None = None


class ClientConfig(BaseModel):
    client_id: str
    algorithm: AlgorithmType = AlgorithmType.sliding_window
    limit: int = Field(10, ge=1)
    window_seconds: int = Field(60, ge=1)
    refill_rate: float = Field(1.0, ge=0.01)


class ClientConfigResponse(BaseModel):
    message: str
    config: ClientConfig


class StatsResponse(BaseModel):
    client_id: str
    algorithm: str
    total_requests: int
    allowed_requests: int
    rejected_requests: int
    rejection_rate_pct: float
