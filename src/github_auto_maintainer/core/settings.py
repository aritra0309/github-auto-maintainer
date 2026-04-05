"""Typed runtime settings for router and model catalog initialization."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_model_catalog_path() -> Path:
    """Return the default absolute path to config/models.yaml."""

    return (Path(__file__).resolve().parents[3] / "config" / "models.yaml").resolve()


class AppSettings(BaseSettings):
    """Environment-backed runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_provider: str = Field(default="openai", validation_alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="gpt-5.4-mini", validation_alias="DEFAULT_MODEL")
    model_catalog_path: Path = Field(
        default_factory=default_model_catalog_path,
        validation_alias="MODEL_CATALOG_PATH",
    )
