"""Tests for Snowflake connection utilities."""

import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_server_snowflake.utils.snowflake_conn import (
    SnowflakeConfig,
    get_snowflake_connection,
    load_private_key,
)


@pytest.fixture
def mock_private_key() -> rsa.RSAPrivateKey:
    """Mock a private key."""
    return MagicMock(spec=rsa.RSAPrivateKey)


@pytest.fixture
def snowflake_config() -> SnowflakeConfig:
    """Create a sample Snowflake configuration."""
    return SnowflakeConfig(
        account="testaccount",
        user="testuser",
        private_key_path="/path/to/key.p8",
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
        role="test_role",
    )


@patch("mcp_server_snowflake.utils.snowflake_conn.load_private_key")
@patch("snowflake.connector.connect")
def test_get_snowflake_connection(
    mock_connect: MagicMock,
    mock_load_key: MagicMock,
    snowflake_config: SnowflakeConfig,
    mock_private_key: rsa.RSAPrivateKey,
) -> None:
    """Test creating a Snowflake connection."""
    # Setup mocks
    mock_load_key.return_value = mock_private_key
    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    # Call function
    conn = get_snowflake_connection(snowflake_config)

    # Assertions
    mock_load_key.assert_called_once_with(snowflake_config.private_key_path)
    mock_connect.assert_called_once_with(
        account=snowflake_config.account,
        user=snowflake_config.user,
        private_key=mock_private_key,
        warehouse=snowflake_config.warehouse,
        database=snowflake_config.database,
        schema=snowflake_config.schema_name,
        role=snowflake_config.role,
    )
    assert conn == mock_connection
