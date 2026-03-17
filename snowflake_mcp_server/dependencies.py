"""Dependency injection providers for FastMCP tools.

These functions are used with FastMCP's Depends() to inject shared
resources (Snowflake connections, config) into tool functions.
The parameters are automatically hidden from the MCP schema.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from snowflake_mcp_server.config import ServerConfig, get_config
from snowflake_mcp_server.utils.async_database import (
    get_isolated_database_ops,
    get_transactional_database_ops,
)
from snowflake_mcp_server.utils.request_context import request_context
from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig

logger = logging.getLogger(__name__)


def get_snowflake_config() -> SnowflakeConfig:
    """Provide Snowflake connection config from environment."""
    config = get_config()
    return SnowflakeConfig(
        account=config.snowflake.account,
        user=config.snowflake.user,
        auth_type=config.snowflake.auth_type,
        private_key_path=config.snowflake.private_key_path,
        warehouse=config.snowflake.warehouse,
        database=config.snowflake.database,
        schema_name=config.snowflake.schema_name,
        role=config.snowflake.role,
    )


def get_server_config() -> ServerConfig:
    """Provide the full server config."""
    return get_config()


@asynccontextmanager
async def get_isolated_db(tool_name: str = "unknown") -> AsyncIterator:
    """Provide an isolated database operations context.

    Usage in tools via Depends():
        async def my_tool(db=Depends(get_isolated_db)):
            rows, cols = await db.execute_query_isolated("SELECT 1")
    """
    async with request_context(tool_name, {}, "fastmcp_client") as ctx:
        async with get_isolated_database_ops(ctx) as db_ops:
            yield db_ops
