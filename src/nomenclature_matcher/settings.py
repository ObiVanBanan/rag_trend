try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - only for a not-yet-installed environment
    import os
    def SettingsConfigDict(**kwargs):
        return kwargs

    class BaseSettings:
        def __init__(self, **values):
            for name, default in self.__class__.__dict__.items():
                if name.startswith("_") or callable(default):
                    continue
                key = name.upper()
                setattr(self, name, values.get(name, os.getenv(key, default)))


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_alias: str = "steel_products_active"
    qdrant_dense_vector_name: str = "dense"
    qdrant_timeout_seconds: float = 5
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 10
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    dense_batch_size: int = 32
    match_top_k: int = 5
    match_score_threshold: float = 0.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
