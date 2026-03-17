"""STDIO entry point for Claude Desktop.

Run via: uv run snowflake-mcp-stdio
Or: python -m snowflake_mcp_server.run_stdio
"""

import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the Snowflake MCP server in STDIO mode."""
    from snowflake_mcp_server.server import mcp

    logger.info("Starting Snowflake MCP server (STDIO mode)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
