import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Sistema de Gestion Escolar"
    environment: str = Field(default="development", alias="APP_ENV")
    secret_key: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    database_url: str = Field(
        default="mysql+pymysql://edu_user:goes-ia-apps%242026@/edu_reg?unix_socket=/cloudsql/goes-ia-apps:us-central1:edu-reg-db",
        alias="DATABASE_URL",
    )
    session_cookie: str = "school_session"
    default_admin_email: str = "admin@antigravity.school"
    default_admin_password: str = "Admin#2026"
    #import_archive_path: str = Field(
    #    default="/app/data/drive-download-20260317T201651Z-1-001.zip",
    #    alias="IMPORT_ARCHIVE_PATH",
    #)
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8080, alias="APP_PORT")
    #auto_bootstrap_data: bool = Field(default=True, alias="AUTO_BOOTSTRAP_DATA")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def mysql_database(self) -> str:
        return os.getenv("MYSQL_DATABASE", "school_management")


@lru_cache
def get_settings() -> Settings:
    return Settings()
