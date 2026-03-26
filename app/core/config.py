from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_VERSION: str = "1.0.0"
    REDIS_URL: str = "redis://localhost:6379"
    DEFAULT_ALGORITHM: str = "sliding_window"  # or "token_bucket"

    class Config:
        env_file = ".env"


settings = Settings()
