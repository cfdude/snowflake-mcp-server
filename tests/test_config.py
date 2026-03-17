"""Tests for configuration loading via pydantic-settings."""

import os
from unittest.mock import patch

import pytest


def _minimal_env():
    """Return minimal env vars needed for config to load."""
    return {
        "SNOWFLAKE_ACCOUNT": "testaccount",
        "SNOWFLAKE_USER": "testuser",
        "SNOWFLAKE_AUTH_TYPE": "private_key",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
    }


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the global config cache between tests."""
    import snowflake_mcp_server.config as cfg
    cfg._config = None
    yield
    cfg._config = None


class TestServerConfig:
    """Test ServerConfig loads from environment variables."""

    def test_loads_minimal_config(self):
        """Config loads with only required env vars set."""
        from snowflake_mcp_server.config import ServerConfig

        with patch.dict(os.environ, _minimal_env(), clear=False):
            config = ServerConfig()
            assert config.snowflake.account == "testaccount"
            assert config.snowflake.user == "testuser"
            assert config.http.port == 8000

    def test_default_values(self):
        """Config applies sensible defaults."""
        from snowflake_mcp_server.config import ServerConfig

        with patch.dict(os.environ, _minimal_env(), clear=False):
            config = ServerConfig()
            assert config.security.readonly_mode is True
            assert config.performance.default_query_limit == 100
            assert config.output.default_output == "auto"

    def test_custom_port_from_env(self):
        """MCP_HTTP_PORT env var overrides default port."""
        from snowflake_mcp_server.config import ServerConfig

        env = {**_minimal_env(), "MCP_HTTP_PORT": "9000"}
        with patch.dict(os.environ, env, clear=False):
            config = ServerConfig()
            assert config.http.port == 9000

    def test_allowed_sql_commands_from_json_env(self):
        """ALLOWED_SQL_COMMANDS parses JSON array string."""
        from snowflake_mcp_server.config import ServerConfig

        env = {**_minimal_env(), "ALLOWED_SQL_COMMANDS": '["select","show","with"]'}
        with patch.dict(os.environ, env, clear=False):
            config = ServerConfig()
            assert config.security.allowed_sql_commands == ["select", "show", "with"]

    def test_default_account_is_empty_string(self):
        """Account defaults to empty string when not set."""
        from snowflake_mcp_server.config import ServerConfig

        with patch.dict(os.environ, {}, clear=True):
            config = ServerConfig()
            assert config.snowflake.account == ""
