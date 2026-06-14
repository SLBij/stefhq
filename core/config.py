from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    database_url: str
    redis_url: str = "redis://localhost:6379"

    secret_key: str
    access_token_expire_minutes: int = 10080  # 7 days

    anthropic_api_key: str

    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "mxbai-embed-large"

    florafolio_url: str = "http://localhost:5173"
    florafolio_headspace_key: str = ""

    curtains_supabase_url: str = "https://sddickbinhposlkatmwi.supabase.co"
    curtains_supabase_key: str = ""

    app_env: str = "development"
    app_debug: bool = True


settings = Settings()
