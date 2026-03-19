"""Tests for MCP tool definitions using FastMCP Client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client


@pytest.fixture
def mock_snowflake_init():
    """Mock Snowflake connection initialization so lifespan doesn't need real credentials."""
    with (
        patch(
            "snowflake_mcp_server.server.connection_manager"
        ) as mock_cm,
        patch(
            "snowflake_mcp_server.server.initialize_connection_pool",
            new_callable=AsyncMock,
        ),
        patch(
            "snowflake_mcp_server.server.close_connection_pool",
            new_callable=AsyncMock,
        ),
    ):
        mock_cm.initialize = MagicMock()
        mock_cm.close = MagicMock()
        yield


class TestToolRegistration:
    """Test that all tools are properly registered."""

    @pytest.mark.asyncio
    async def test_server_has_all_tools(self, mock_snowflake_init):
        """Server exposes all 5 Snowflake tools."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert tool_names == {
                "list_databases",
                "list_views",
                "describe_view",
                "query_view",
                "execute_query",
            }

    @pytest.mark.asyncio
    async def test_list_databases_tool_schema(self, mock_snowflake_init):
        """list_databases has no required parameters."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            db_tool = next(t for t in tools if t.name == "list_databases")
            required = db_tool.inputSchema.get("required", [])
            assert required == []

    @pytest.mark.asyncio
    async def test_execute_query_tool_schema(self, mock_snowflake_init):
        """execute_query requires 'query' parameter."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            eq_tool = next(t for t in tools if t.name == "execute_query")
            assert "query" in eq_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_tool_annotations_are_readonly(self, mock_snowflake_init):
        """All tools should have readOnlyHint=True annotation."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            for tool in tools:
                if tool.annotations:
                    assert tool.annotations.readOnlyHint is True
