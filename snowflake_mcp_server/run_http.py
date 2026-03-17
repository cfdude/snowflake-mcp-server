"""HTTP entry point for PM2 / Claude Code.

Run via: uv run snowflake-mcp-http
Or: python -m snowflake_mcp_server.run_http
Or: pm2 start ecosystem.config.js

Serves Streamable HTTP transport on port 8000 (configurable via MCP_HTTP_PORT).
Stateless HTTP mode eliminates server-side session state for memory efficiency.
"""

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the Snowflake MCP server in HTTP mode."""
    from snowflake_mcp_server.config import get_config
    from snowflake_mcp_server.server import mcp

    config = get_config()
    host = config.http.host
    port = config.http.port

    # Allow CLI overrides: --host and --port
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    logger.info(f"Starting Snowflake MCP server (HTTP mode) on {host}:{port}")
    logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
    logger.info(f"Health check: http://{host}:{port}/health")

    mcp.run(transport="http", host=host, port=port, stateless_http=True)


if __name__ == "__main__":
    main()
