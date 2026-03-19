"""Environment-based configuration management for Snowflake MCP Server.

Uses pydantic-settings BaseSettings to auto-load from environment variables.
Each nested config class has an env_prefix that maps to env var names.
"""

import logging
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SnowflakeConnectionConfig(BaseSettings):
    """Snowflake connection configuration."""

    model_config = SettingsConfigDict(env_prefix="SNOWFLAKE_")

    account: str = ""
    user: str = ""
    auth_type: str = "private_key"
    private_key_path: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    private_key: Optional[str] = None
    warehouse: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = None
    role: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None

    @field_validator("auth_type", mode="before")
    @classmethod
    def normalize_auth_type(cls, v: str) -> str:
        return v.lower()


class ConnectionPoolConfig(BaseSettings):
    """Connection pool configuration."""

    model_config = SettingsConfigDict(env_prefix="SNOWFLAKE_POOL_")

    min_size: int = 2
    max_size: int = 10
    connection_timeout: float = 30.0
    health_check_interval: int = 5
    max_inactive_time: int = 30
    refresh_hours: int = 8

    @field_validator("max_size", mode="before")
    @classmethod
    def validate_max_size(cls, v: int) -> int:
        if int(v) < 1:
            raise ValueError("max_size must be at least 1")
        return int(v)


class HttpServerConfig(BaseSettings):
    """HTTP server configuration."""

    model_config = SettingsConfigDict(env_prefix="MCP_HTTP_")

    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout: int = 300

    @field_validator("port", mode="before")
    @classmethod
    def validate_port(cls, v: int) -> int:
        v = int(v)
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v


class PerformanceConfig(BaseSettings):
    """Performance and resource configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    max_concurrent_requests: int = 10
    default_query_limit: int = 100
    max_query_limit: int = 10000


class SecurityConfig(BaseSettings):
    """Security configuration.

    Note: ALLOWED_SQL_COMMANDS env var must be JSON format: '["select","show"]'
    """

    model_config = SettingsConfigDict(env_prefix="")

    allowed_sql_commands: List[str] = [
        "select", "show", "describe", "explain", "with", "union", "use",
    ]
    readonly_mode: bool = True

    @field_validator("allowed_sql_commands", mode="after")
    @classmethod
    def normalize_sql_commands(cls, v: List[str]) -> List[str]:
        return [cmd.lower().strip() for cmd in v if cmd and cmd.strip()]


class OutputConfig(BaseSettings):
    """Output and token management configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    model_name: str = "unknown"
    model_token_limit: int = 100000
    safety_margin: float = 0.7
    default_output: str = "auto"
    default_file_format: str = "csv"
    default_output_dir: str = "./query_results"
    client_root: Optional[str] = None
    screen_output_row_threshold: int = 1000
    auto_generate_filename: bool = True
    filename_pattern: str = "query_{date}_{time}"
    token_sample_size: int = 100
    log_token_estimation: bool = False

    @field_validator("default_output", mode="before")
    @classmethod
    def validate_output_mode(cls, v: str) -> str:
        if v not in ("auto", "screen", "file"):
            raise ValueError("default_output must be 'auto', 'screen', or 'file'")
        return v

    @field_validator("default_file_format", mode="before")
    @classmethod
    def validate_file_format(cls, v: str) -> str:
        if v not in ("csv", "json"):
            raise ValueError("default_file_format must be 'csv' or 'json'")
        return v


class ServerConfig(BaseSettings):
    """Complete server configuration.

    Nested models are instantiated automatically from env vars.
    Example: SNOWFLAKE_ACCOUNT, MCP_HTTP_PORT, ALLOWED_SQL_COMMANDS, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
    )

    environment: str = "production"
    app_version: str = "1.0.0"

    snowflake: SnowflakeConnectionConfig = Field(default_factory=SnowflakeConnectionConfig)
    pool: ConnectionPoolConfig = Field(default_factory=ConnectionPoolConfig)
    http: HttpServerConfig = Field(default_factory=HttpServerConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


# Global configuration instance
_config: Optional[ServerConfig] = None


def get_config() -> ServerConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = ServerConfig()
    return _config


def reload_config() -> ServerConfig:
    """Reload configuration from environment variables."""
    global _config
    _config = ServerConfig()
    return _config
