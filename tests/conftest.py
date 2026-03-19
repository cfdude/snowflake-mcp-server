"""Shared pytest fixtures for Snowflake MCP server tests."""

import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the global config cache between tests."""
    import snowflake_mcp_server.config as cfg

    cfg._config = None
    yield
    cfg._config = None


@pytest.fixture(autouse=True)
def mock_snowflake_env():
    """Provide minimal Snowflake env vars for all tests."""
    env = {
        "SNOWFLAKE_ACCOUNT": "testaccount",
        "SNOWFLAKE_USER": "testuser",
        "SNOWFLAKE_AUTH_TYPE": "private_key",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def mock_private_key():
    """Mock RSA private key."""
    return MagicMock(spec=rsa.RSAPrivateKey)


@pytest.fixture
def mock_snowflake_connection():
    """Mock Snowflake connection object."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.description = []
    conn.cursor.return_value = cursor
    return conn
