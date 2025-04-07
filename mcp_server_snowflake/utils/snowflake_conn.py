"""Snowflake connection utilities.

This module provides utility functions and classes for establishing secure connections
to Snowflake using either service account authentication with private key or external
browser authentication. It handles the configuration parsing and security aspects of
connecting to Snowflake databases.

The primary components are:
- SnowflakeConfig: A Pydantic model that validates connection parameters
- load_private_key: A function to securely load RSA private keys
- get_snowflake_connection: A function that establishes connections using key pair
  auth or browser auth
"""

from enum import Enum
from typing import Any, Dict, Optional

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel, ValidationInfo, field_validator
from snowflake.connector import SnowflakeConnection


class AuthType(str, Enum):
    """Authentication types for Snowflake."""

    PRIVATE_KEY = "private_key"
    EXTERNAL_BROWSER = "external_browser"


class SnowflakeConfig(BaseModel):
    """Snowflake connection configuration."""

    account: str
    user: str
    auth_type: AuthType
    private_key_path: Optional[str] = None
    warehouse: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = (
        None  # Renamed from schema to avoid conflict with BaseModel
    )
    role: Optional[str] = None

    @field_validator("private_key_path")
    @classmethod
    def validate_private_key_path(
        cls, v: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        """Validate that private_key_path is provided when auth_type is PRIVATE_KEY."""
        values = info.data
        if values.get("auth_type") == AuthType.PRIVATE_KEY and not v:
            raise ValueError(
                "private_key_path is required when auth_type is PRIVATE_KEY"
            )
        return v


def load_private_key(private_key_path: str) -> rsa.RSAPrivateKey:
    """Load private key from file."""
    with open(private_key_path, "rb") as key_file:
        p_key = load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend(),
        )
        if not isinstance(p_key, rsa.RSAPrivateKey):
            raise TypeError("Private key is not an RSA private key")
        return p_key


def get_snowflake_connection(config: SnowflakeConfig) -> SnowflakeConnection:
    """Create a connection to Snowflake using key pair or external browser auth."""
    conn_params: Dict[str, Any] = {
        "account": config.account,
        "user": config.user,
    }

    # Set authentication parameters based on auth_type
    if config.auth_type == AuthType.PRIVATE_KEY:
        if not config.private_key_path:
            raise ValueError(
                "Private key path is required for private key authentication"
            )
        private_key = load_private_key(config.private_key_path)
        conn_params["private_key"] = private_key
    elif config.auth_type == AuthType.EXTERNAL_BROWSER:
        conn_params["authenticator"] = "externalbrowser"

    # Add optional connection parameters
    if config.warehouse:
        conn_params["warehouse"] = config.warehouse
    if config.database:
        conn_params["database"] = config.database
    if config.schema_name:
        conn_params["schema"] = config.schema_name
    if config.role:
        conn_params["role"] = config.role

    return snowflake.connector.connect(**conn_params)
