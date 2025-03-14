"""Snowflake connection utilities.

This module provides utility functions and classes for establishing secure connections 
to Snowflake using service account authentication with private key. It handles the 
configuration parsing and security aspects of connecting to Snowflake databases.

The primary components are:
- SnowflakeConfig: A Pydantic model that validates connection parameters
- load_private_key: A function to securely load RSA private keys
- get_snowflake_connection: A function that establishes connections using key pair auth
"""

import os
from typing import Any, Dict, Optional

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel
from snowflake.connector import SnowflakeConnection


class SnowflakeConfig(BaseModel):
    """Snowflake connection configuration."""

    account: str
    user: str
    private_key_path: str
    warehouse: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = (
        None  # Renamed from schema to avoid conflict with BaseModel
    )
    role: Optional[str] = None


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
    """Create a connection to Snowflake using key pair authentication."""
    private_key = load_private_key(config.private_key_path)

    conn_params: Dict[str, Any] = {
        "account": config.account,
        "user": config.user,
        "private_key": private_key,
    }

    if config.warehouse:
        conn_params["warehouse"] = config.warehouse
    if config.database:
        conn_params["database"] = config.database
    if config.schema_name:
        conn_params["schema"] = config.schema_name
    if config.role:
        conn_params["role"] = config.role

    return snowflake.connector.connect(**conn_params)
