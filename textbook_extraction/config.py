"""Configuration via environment variables and .env file."""
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Global LLM defaults (overridden by per-worker settings below)
    provider: str = Field(default="anthropic", alias="TEXTBOOK_PROVIDER")
    model: str = Field(default="claude-opus-4-6", alias="TEXTBOOK_MODEL")

    # Per-worker provider overrides (fall back to TEXTBOOK_PROVIDER if empty)
    w2_provider: str = Field(default="", alias="W2_PROVIDER")
    w3_provider: str = Field(default="", alias="W3_PROVIDER")
    w5_provider: str = Field(default="", alias="W5_PROVIDER")
    w7_provider: str = Field(default="", alias="W7_PROVIDER")
    w8_provider: str = Field(default="", alias="W8_PROVIDER")

    # Per-worker model overrides (fall back to TEXTBOOK_MODEL if empty)
    w2_model: str = Field(default="", alias="W2_MODEL")
    w3_model: str = Field(default="", alias="W3_MODEL")
    w5_model: str = Field(default="", alias="W5_MODEL")
    w7_model: str = Field(default="", alias="W7_MODEL")
    w8_model: str = Field(default="", alias="W8_MODEL")

    # Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Kimi / OpenAI-compatible provider
    kimi_api_key: str = Field(default="", alias="KIMI_API_KEY")
    openai_compat_base_url: str = Field(default="https://api.moonshot.ai/v1", alias="OPENAI_COMPAT_BASE_URL")

    # AWS
    aws_region: str = Field(default="us-west-1", alias="AWS_REGION")
    output_bucket: str = Field(default="textbook-extraction-output", alias="TEXTBOOK_OUTPUT_BUCKET")

    # Global model defaults
    max_tokens: int = 16384
    max_retries: int = 5
    retry_base_delay: float = 2.0

    # PDF rendering
    image_dpi: int = Field(default="200", alias="TEXTBOOK_IMAGE_DPI")

    # Worker tuning
    w5_max_concurrency: int = 10

    def get_worker_provider(self, worker_name: str) -> str:
        """Resolve the provider for a given worker, falling back to global default."""
        short = worker_name.split("_")[0]  # "w2_toc_raw" -> "w2"
        override = getattr(self, f"{short}_provider", "")
        return override if override else self.provider

    def get_worker_model(self, worker_name: str) -> str:
        """Resolve the model for a given worker, falling back to global default."""
        short = worker_name.split("_")[0]
        override = getattr(self, f"{short}_model", "")
        return override if override else self.model
