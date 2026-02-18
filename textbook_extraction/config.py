"""Configuration via environment variables and .env file."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # API key reads ANTHROPIC_API_KEY (no prefix)
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")

    # AWS
    aws_region: str = Field(default="us-west-1", alias="AWS_REGION")
    output_bucket: str = Field(default="textbook-extraction-output", alias="TEXTBOOK_OUTPUT_BUCKET")

    # Claude
    model: str = Field(default="claude-opus-4-6", alias="TEXTBOOK_MODEL")
    max_tokens: int = 16384
    max_retries: int = 5
    retry_base_delay: float = 2.0

    # PDF rendering
    image_dpi: int = Field(default=200, alias="TEXTBOOK_IMAGE_DPI")

    # Worker tuning
    w5_max_concurrency: int = 10
