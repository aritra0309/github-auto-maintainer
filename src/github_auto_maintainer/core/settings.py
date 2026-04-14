"""Typed runtime settings for router and model catalog initialization."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Environment-backed runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_provider: str = Field(default="", validation_alias="DEFAULT_PROVIDER")
    default_model: str = Field(default="", validation_alias="DEFAULT_MODEL")
