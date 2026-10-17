from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres
    postgres_user: str = "masi"
    postgres_password: str = "masi"
    postgres_db: str = "masi_ai"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Scheduling / behaviour
    tz: str = "Africa/Casablanca"
    orderbook_interval_min: int = 5
    news_interval_min: int = 30

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
