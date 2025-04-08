"""Snowflake connection utilities.

This module provides utility functions and classes for establishing secure connections
to Snowflake using either service account authentication with private key or external
browser authentication. It handles the configuration parsing and security aspects of
connecting to Snowflake databases.

The primary components are:
- SnowflakeConfig: A Pydantic model that validates connection parameters
- SnowflakeConnectionManager: A singleton class that manages persistent connections
- load_private_key: A function to securely load RSA private keys
- get_snowflake_connection: A function that establishes connections using key pair
  auth or browser auth
"""

import os
import threading
import time
from datetime import datetime, timedelta
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


class SnowflakeConnectionManager:
    """Singleton manager for Snowflake connection pooling and refresh.

    This class maintains a persistent connection to Snowflake and refreshes it
    periodically in the background based on the configured refresh interval.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SnowflakeConnectionManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """Initialize the connection manager if not already initialized."""
        if self._initialized:
            return

        self._connection = None
        self._connection_lock = threading.Lock()
        self._config = None
        self._last_refresh_time = None
        self._refresh_thread = None
        self._stop_event = threading.Event()

        # Get refresh interval from environment variable (in hours, default 8)
        refresh_interval_hours = float(os.getenv("SNOWFLAKE_CONN_REFRESH_HOURS", "8"))
        self._refresh_interval = timedelta(hours=refresh_interval_hours)

        self._initialized = True

    def initialize(self, config: SnowflakeConfig) -> None:
        """Initialize the connection manager with the given configuration."""
        with self._connection_lock:
            self._config = config
            self._connect()

            # Start background refresh thread if not already running
            if self._refresh_thread is None or not self._refresh_thread.is_alive():
                self._stop_event.clear()
                self._refresh_thread = threading.Thread(
                    target=self._refresh_connection_periodically,
                    daemon=True
                )
                self._refresh_thread.start()

    def get_connection(self) -> SnowflakeConnection:
        """Get the current Snowflake connection, creating it if necessary."""
        with self._connection_lock:
            if self._connection is None:
                if self._config is None:
                    raise ValueError("Connection manager not initialized with a configuration")
                self._connect()
            return self._connection

    def close(self) -> None:
        """Close the current connection and stop the refresh thread."""
        self._stop_event.set()

        with self._connection_lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass  # Ignore errors during close
                self._connection = None

    def _connect(self) -> None:
        """Establish a new connection to Snowflake."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass  # Ignore errors during close

        self._connection = get_snowflake_connection(self._config)
        self._last_refresh_time = datetime.now()

    def _refresh_connection_periodically(self) -> None:
        """Background thread that refreshes the connection periodically."""
        while not self._stop_event.is_set():
            # Sleep for a short interval and check if it's time to refresh
            if self._stop_event.wait(60):  # Check every minute
                break

            with self._connection_lock:
                if (
                    self._last_refresh_time is not None
                    and datetime.now() - self._last_refresh_time >= self._refresh_interval
                ):
                    try:
                        print(f"Refreshing Snowflake connection (interval: {self._refresh_interval})")
                        self._connect()
                    except Exception as e:
                        print(f"Error refreshing Snowflake connection: {e}")


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


# Create a singleton instance for convenience
connection_manager = SnowflakeConnectionManager()
