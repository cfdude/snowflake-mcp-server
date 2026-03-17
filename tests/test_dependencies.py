"""Tests for dependency injection providers."""

import os
from unittest.mock import patch

import pytest


class TestGetSnowflakeConfig:
    """Test Snowflake config dependency."""

    def test_returns_config_from_env(self):
        """get_snowflake_config returns a SnowflakeConfig from env."""
        import snowflake_mcp_server.config as cfg
        cfg._config = None

        env = {
            "SNOWFLAKE_ACCOUNT": "testaccount",
            "SNOWFLAKE_USER": "testuser",
            "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
        }
        with patch.dict(os.environ, env, clear=False):
            from snowflake_mcp_server.dependencies import get_snowflake_config

            config = get_snowflake_config()
            assert config.account == "testaccount"
            assert config.user == "testuser"

        cfg._config = None
