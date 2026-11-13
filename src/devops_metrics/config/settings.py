"""
Application Settings Module
"""
import os
from typing import Optional, Literal
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator
from dotenv import load_dotenv
from .access import checked_path, validate_database_url, validate_org

# Never discover credentials by walking into parent folders or interpolating values.
load_dotenv(dotenv_path=checked_path(Path.cwd() / ".env"), interpolate=False)

class Settings(BaseSettings):
    """Application settings"""

    # Azure DevOps Settings
    azure_devops_pat: SecretStr = Field(..., description="Azure DevOps Personal Access Token")
    azure_devops_org: str = Field(..., description="Azure DevOps Organization name")

    # Logging Settings
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format"
    )
    log_file: Optional[Path] = Field(default=None, description="Optional log file path")

    # Time-weighting configuration
    current_month_weight: float = Field(default=0.8, description="Weight for current month data")
    historical_weight: float = Field(default=0.2, description="Weight for historical data")

    # Thresholds and performance controls
    thresholds_file: Optional[Path] = Field(default=Path(__file__).parent / "thresholds.yaml", description="Path to thresholds YAML")
    rate_limit_rps: int = Field(default=20, description="Global rate limit (requests per second)")
    concurrency_repos: int = Field(default=10, description="Max concurrent calls for repository-scoped endpoints")
    concurrency_builds: int = Field(default=20, description="Max concurrent calls for build/test endpoints")
    concurrency_expensive: int = Field(default=10, description="Max concurrent calls for expensive fan-out endpoints")

    # Structured logging toggle
    log_json: bool = Field(default=False, description="Enable JSON logging format")

    # Runtime environment and database
    run_env: Literal["local", "ci", "dev", "prod"] = Field(default="local", description="Runtime environment (local|ci|dev|prod)")
    database_url: Optional[str] = Field(default=None, description="Database connection URL supplied through local configuration")

    # Optional API key for HTTP API authentication (header: X-API-Key)
    api_key: Optional[SecretStr] = Field(default=None, description="Optional API key for authentication")

    # Exemptions configuration (foundation)
    exemptions_file: Optional[Path] = Field(
        default=(Path(__file__).parent / "exemptions.yaml"),
        description="Path to project exemptions YAML file",
    )

    _validate_org = field_validator("azure_devops_org")(validate_org)

    @field_validator("thresholds_file", "exemptions_file", "log_file")
    @classmethod
    def validate_configured_path(cls, value: Optional[Path]) -> Optional[Path]:
        return checked_path(value) if value is not None else None

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: Optional[str]) -> Optional[str]:
        return validate_database_url(value) if value else value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="ADO_",
        # The shared local .env also holds Compose/Grafana settings.
        extra="ignore",
        hide_input_in_errors=True,
    )
