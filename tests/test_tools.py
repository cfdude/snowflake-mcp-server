"""Tests for MCP tool definitions using FastMCP Client."""

import pytest
from fastmcp import Client


class TestToolRegistration:
    """Test that all tools are properly registered."""

    @pytest.mark.asyncio
    async def test_server_has_all_tools(self):
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
    async def test_list_databases_tool_schema(self):
        """list_databases has no required parameters."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            db_tool = next(t for t in tools if t.name == "list_databases")
            required = db_tool.inputSchema.get("required", [])
            assert required == []

    @pytest.mark.asyncio
    async def test_execute_query_tool_schema(self):
        """execute_query requires 'query' parameter."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            eq_tool = next(t for t in tools if t.name == "execute_query")
            assert "query" in eq_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_tool_annotations_are_readonly(self):
        """All tools should have readOnlyHint=True annotation."""
        from snowflake_mcp_server.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            for tool in tools:
                if tool.annotations:
                    assert tool.annotations.readOnlyHint is True
