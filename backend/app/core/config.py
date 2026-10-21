"""Application settings, loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    app_name: str = "ResearchFlow"
    app_env: Literal["dev", "test", "prod"] = "dev"
    secret_key: str = ""
    jwt_expire_minutes: int = 1440
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"  # comma-separated

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "researchflow"
    postgres_user: str = "researchflow"
    postgres_password: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "researchflow"
    minio_secure: bool = False
    # Host the *browser* uses to reach MinIO; presigned download URLs are signed for it
    minio_public_endpoint: str = ""
    minio_public_secure: bool = False

    # Uploads
    max_upload_mb: int = 50
    download_url_expiry_s: int = 900

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # GROBID
    grobid_url: str = "http://localhost:8070"

    # LLM — every model call goes through app/services/llm.py and embeddings.py
    llm_provider: Literal["gemini", "openai", "fake"] = "gemini"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "gemini-3.8-flash"  # reasoning / writing
    llm_model_fast: str = "gemini-3.5-flash-lite"  # extraction / classification
    # Used when the requested model keeps failing with transient errors (e.g. "high demand" 503s); "" disables
    llm_model_fallback: str = "gemini-3.5-flash-lite"
    llm_retries_before_fallback: int = 1  # the last model in the chain gets the full llm_max_retries
    llm_timeout_s: float = 120.0  # per attempt, for the last model in the chain
    # Per attempt for a model that has a fallback: an overloaded model is abandoned quickly
    llm_primary_timeout_s: float = 45.0
    llm_max_retries: int = 3
    embedding_provider: Literal["gemini", "openai", "fake"] = "gemini"
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 1536  # must match paper_chunks.embedding vector(N)
    embedding_batch_size: int = 100

    # Academic APIs
    s2_api_key: str = ""
    openalex_email: str = ""
    crossref_email: str = ""
    unpaywall_email: str = ""

    # Dependency health checks
    health_check_timeout_s: float = 3.0

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def psycopg_url(self) -> str:
        """Plain libpq URL for psycopg (used by the LangGraph checkpointer)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @model_validator(mode="after")
    def _require_secret_in_prod(self) -> "Settings":
        if self.app_env == "prod" and len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be set (>= 32 chars) when APP_ENV=prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
