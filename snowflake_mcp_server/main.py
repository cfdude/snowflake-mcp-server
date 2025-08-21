"""MCP server implementation for Snowflake.

This module provides a Model Context Protocol (MCP) server that allows Claude
to perform read-only operations against Snowflake databases. It connects to
Snowflake using either service account authentication with a private key or
external browser authentication. It exposes various tools for querying database
metadata and data, including support for multi-view and multi-database queries.

The server is designed to be used with Claude Desktop as an MCP server, providing
Claude with secure, controlled access to Snowflake data for analysis and reporting.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import wraps
from typing import Any, Dict, List, Optional, Sequence, Union

import anyio
import mcp.types as mcp_types
import sqlglot
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from sqlglot.errors import ParseError

from snowflake_mcp_server.config import get_config
from snowflake_mcp_server.utils.async_database import (
    get_isolated_database_ops,
    get_transactional_database_ops,
)
from snowflake_mcp_server.utils.async_pool import ConnectionPoolConfig
from snowflake_mcp_server.utils.contextual_logging import (
    log_request_complete,
    log_request_error,
    log_request_start,
    setup_server_logging,
)
from snowflake_mcp_server.utils.output_handler import ResultOutputHandler
from snowflake_mcp_server.utils.request_context import RequestContext, request_context
from snowflake_mcp_server.utils.snowflake_conn import (
    AuthType,
    SnowflakeConfig,
    connection_manager,
)

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


# Initialize Snowflake configuration from environment variables
def get_snowflake_config() -> SnowflakeConfig:
    """Load Snowflake configuration from environment variables."""
    auth_type_str = os.getenv("SNOWFLAKE_AUTH_TYPE", "private_key").lower()
    auth_type = (
        AuthType.PRIVATE_KEY
        if auth_type_str == "private_key"
        else AuthType.EXTERNAL_BROWSER
    )

    config = SnowflakeConfig(
        account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
        user=os.getenv("SNOWFLAKE_USER", ""),
        auth_type=auth_type,
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema_name=os.getenv("SNOWFLAKE_SCHEMA"),
        role=os.getenv("SNOWFLAKE_ROLE"),
    )

    # Only set private_key_path if using private key authentication
    if auth_type == AuthType.PRIVATE_KEY:
        config.private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", "")

    return config


def get_pool_config() -> ConnectionPoolConfig:
    """Load connection pool configuration from environment."""
    return ConnectionPoolConfig(
        min_size=int(os.getenv("SNOWFLAKE_POOL_MIN_SIZE", "2")),
        max_size=int(os.getenv("SNOWFLAKE_POOL_MAX_SIZE", "10")),
        max_inactive_time=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES", "30"))),
        health_check_interval=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES", "5"))),
        connection_timeout=float(os.getenv("SNOWFLAKE_POOL_CONNECTION_TIMEOUT", "30.0")),
        retry_attempts=int(os.getenv("SNOWFLAKE_POOL_RETRY_ATTEMPTS", "3")),
    )


async def initialize_async_infrastructure() -> None:
    """Initialize async connection infrastructure."""
    snowflake_config = get_snowflake_config()
    pool_config = get_pool_config()
    
    from .utils.async_pool import initialize_connection_pool
    
    await initialize_connection_pool(snowflake_config, pool_config)


@asynccontextmanager
async def get_database_connection() -> Any:
    """Dependency injection for database connections."""
    from .utils.async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        yield connection


def with_request_isolation(tool_name: str) -> Any:
    """Decorator to add request isolation to MCP handlers."""
    def decorator(handler_func: Any) -> Any:
        @wraps(handler_func)
        async def wrapper(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
            # Extract client ID from arguments or headers if available
            client_id = arguments.get("_client_id", "unknown") if arguments else "unknown"
            
            async with request_context(tool_name, arguments or {}, client_id) as ctx:
                try:
                    # Log request start
                    log_request_start(ctx.request_id, tool_name, client_id, arguments or {})
                    
                    # Call original handler with request context (ctx is guaranteed to be RequestContext)
                    result = await handler_func(name, arguments, ctx)
                    
                    # Log successful completion
                    duration = ctx.get_duration_ms()
                    if duration is not None:
                        log_request_complete(ctx.request_id, duration, ctx.metrics.queries_executed)
                    
                    return result
                    
                except Exception as e:
                    # Log error and add to context
                    log_request_error(ctx.request_id, e, f"handler_{tool_name}")
                    ctx.add_error(e, f"handler_{tool_name}")
                    # Re-raise to maintain original error handling
                    raise
        return wrapper
    return decorator


# Initialize the connection manager at startup
def init_connection_manager() -> None:
    """Initialize the connection manager with Snowflake config."""
    config = get_snowflake_config()
    connection_manager.initialize(config)


# Define MCP server
def create_server() -> Server:
    """Create and configure the MCP server."""
    # Initialize the connection manager before setting up the server
    init_connection_manager()

    server: Server = Server(
        name="snowflake-mcp-server",
        version="0.2.0",
        instructions="MCP server for performing read-only operations against "
        "Snowflake.",
    )

    return server


# Snowflake query handler functions
@with_request_isolation("list_databases")
async def handle_list_databases(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: RequestContext = None  # type: ignore
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to list all accessible Snowflake databases with isolation."""
    try:
        async with get_isolated_database_ops(request_ctx) as db_ops:
            results, _ = await db_ops.execute_query_isolated("SHOW DATABASES")
            
            databases = [row[1] for row in results]
            
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Available Snowflake databases:\n" + "\n".join(databases),
                )
            ]

    except Exception as e:
        logger.error(f"Error querying databases: {e}")
        return [
            mcp_types.TextContent(
                type="text", text=f"Error querying databases: {str(e)}"
            )
        ]


@with_request_isolation("list_views")
async def handle_list_views(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: RequestContext = None  # type: ignore
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to list views with request isolation."""
    try:
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None

        if not database:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: database parameter is required"
                )
            ]

        async with get_isolated_database_ops(request_ctx) as db_ops:
            # Set database context in isolation
            await db_ops.use_database_isolated(database)
            
            # Handle schema context
            if schema:
                await db_ops.use_schema_isolated(schema)
            else:
                # Get current schema in this isolated context
                _, current_schema = await db_ops.get_current_context()
                schema = current_schema

            # Execute views query in isolated context
            results, _ = await db_ops.execute_query_isolated(f"SHOW VIEWS IN {database}.{schema}")

            # Process results
            views = []
            for row in results:
                view_name = row[1]
                created_on = row[5]
                views.append(f"{view_name} (created: {created_on})")

            if views:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"Views in {database}.{schema}:\n" + "\n".join(views),
                    )
                ]
            else:
                return [
                    mcp_types.TextContent(
                        type="text", text=f"No views found in {database}.{schema}"
                    )
                ]

    except Exception as e:
        logger.error(f"Error listing views: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error listing views: {str(e)}")
        ]


@with_request_isolation("describe_view")
async def handle_describe_view(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: RequestContext = None  # type: ignore
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to describe the structure of a view with isolation."""
    try:
        # Extract arguments
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        view_name = arguments.get("view_name") if arguments else None

        if not database or not view_name:
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Error: database and view_name parameters are required",
                )
            ]

        async with get_isolated_database_ops(request_ctx) as db_ops:
            # Set database context in isolation
            await db_ops.use_database_isolated(database)
            
            # Use the provided schema or use default schema
            if schema:
                await db_ops.use_schema_isolated(schema)
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                # Get the current schema
                _, current_schema = await db_ops.get_current_context()
                if current_schema and current_schema != "Unknown":
                    schema = current_schema
                    full_view_name = f"{database}.{schema}.{view_name}"
                else:
                    return [
                        mcp_types.TextContent(
                            type="text", text="Error: Could not determine current schema"
                        )
                    ]

            # Execute query to describe view
            describe_results, _ = await db_ops.execute_query_isolated(f"DESCRIBE VIEW {full_view_name}")

            # Process column results
            columns = []
            for row in describe_results:
                col_name = row[0]
                col_type = row[1]
                col_null = "NULL" if row[3] == "Y" else "NOT NULL"
                columns.append(f"{col_name} : {col_type} {col_null}")

            # Get view definition
            ddl_results, _ = await db_ops.execute_query_isolated(f"SELECT GET_DDL('VIEW', '{full_view_name}')")
            view_ddl = ddl_results[0][0] if ddl_results and ddl_results[0] else "Definition not available"

            if columns:
                result = f"## View: {full_view_name}\n\n"
                result += f"Request ID: {request_ctx.request_id}\n\n"
                result += "### Columns:\n"
                for col in columns:
                    result += f"- {col}\n"

                result += "\n### View Definition:\n```sql\n"
                result += view_ddl
                result += "\n```"

                return [mcp_types.TextContent(type="text", text=result)]
            else:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"View {full_view_name} not found or you don't have permission to access it.",
                    )
                ]

    except Exception as e:
        logger.error(f"Error describing view: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error describing view: {str(e)}")
        ]


@with_request_isolation("query_view")
async def handle_query_view(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: RequestContext = None  # type: ignore
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to query data from a view with isolation."""
    try:
        # Extract arguments
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        view_name = arguments.get("view_name") if arguments else None
        limit = (
            int(arguments.get("limit", 10))
            if arguments and arguments.get("limit") is not None
            else 10
        )  # Default limit to 10 rows

        if not database or not view_name:
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Error: database and view_name parameters are required",
                )
            ]

        async with get_isolated_database_ops(request_ctx) as db_ops:
            # Set database context in isolation
            await db_ops.use_database_isolated(database)
            
            # Use the provided schema or use default schema
            if schema:
                await db_ops.use_schema_isolated(schema)
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                # Get the current schema
                _, current_schema = await db_ops.get_current_context()
                if current_schema and current_schema != "Unknown":
                    schema = current_schema
                    full_view_name = f"{database}.{schema}.{view_name}"
                else:
                    return [
                        mcp_types.TextContent(
                            type="text", text="Error: Could not determine current schema"
                        )
                    ]

            # Execute query to get data from view
            rows, column_names = await db_ops.execute_query_limited(f"SELECT * FROM {full_view_name}", limit)

            if rows:
                # Format the results as a markdown table
                result = f"## Data from {full_view_name} (Showing {len(rows)} rows)\n\n"
                result += f"Request ID: {request_ctx.request_id}\n\n"

                # Create header row
                result += "| " + " | ".join(column_names) + " |\n"
                result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

                # Add data rows
                for row in rows:
                    formatted_values = []
                    for val in row:
                        if val is None:
                            formatted_values.append("NULL")
                        else:
                            # Format the value as string and escape any pipe characters
                            val_str = str(val).replace("|", "\\|")
                            if len(val_str) > 200:  # Truncate long values
                                val_str = val_str[:197] + "..."
                            formatted_values.append(val_str)
                    result += "| " + " | ".join(formatted_values) + " |\n"

                return [mcp_types.TextContent(type="text", text=result)]
            else:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"No data found in view {full_view_name} or the view is empty.",
                    )
                ]

    except Exception as e:
        logger.error(f"Error querying view: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error querying view: {str(e)}")
        ]


@with_request_isolation("execute_query")
async def handle_execute_query(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: RequestContext = None  # type: ignore
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to execute read-only SQL queries with smart output handling."""
    try:
        # Extract arguments
        query = arguments.get("query") if arguments else None
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        limit_rows = (
            int(arguments.get("limit", 100))
            if arguments and arguments.get("limit") is not None
            else 100
        )  # Default limit to 100 rows
        
        # New output parameters with precedence handling
        output_mode = arguments.get("output")  # Could be None
        file_format = arguments.get("format")  # Could be None  
        location = arguments.get("location")   # Could be None
        filename = arguments.get("filename")   # Could be None
        
        # Transaction control parameters (for read-only operations)
        use_transaction = arguments.get("use_transaction", False) if arguments else False
        auto_commit = arguments.get("auto_commit", True) if arguments else True

        if not query:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: query parameter is required"
                )
            ]

        # Load configuration
        config = get_config()
        output_config = config.output
        
        # Apply defaults from environment ONLY if AI didn't specify
        if output_mode is None:
            output_mode = output_config.default_output
        if file_format is None:
            file_format = output_config.default_file_format
        if location is None:
            location = output_config.default_output_dir

        # Initialize output handler
        output_handler = ResultOutputHandler(output_config)

        # Validate output parameters
        is_valid, error_msg = output_handler.validate_output_parameters(
            output_mode, file_format, location, filename
        )
        if not is_valid:
            return [
                mcp_types.TextContent(
                    type="text", text=f"Error: {error_msg}"
                )
            ]

        # Validate that the query uses allowed SQL commands
        try:
            allowed_types = set(config.security.allowed_sql_commands)
            parsed_statements = sqlglot.parse(query, dialect="snowflake")

            if not parsed_statements:
                raise ParseError("Error: Could not parse SQL query")

            for stmt in parsed_statements:
                if (
                    stmt is not None
                    and hasattr(stmt, "key")
                    and stmt.key
                ):
                    stmt_key = stmt.key.lower()
                    
                    # Special handling for commands that sqlglot parses as "command" type
                    if stmt_key == "command" and hasattr(stmt, "this") and stmt.this:
                        command_text = str(stmt.this).strip().upper()
                        if command_text.startswith("CALL"):
                            stmt_key = "call"
                        elif command_text.startswith("SHOW"):
                            stmt_key = "show"
                        elif command_text.startswith("DESCRIBE") or command_text.startswith("DESC"):
                            stmt_key = "describe"
                    
                    if stmt_key not in allowed_types:
                        allowed_commands_str = ", ".join(sorted(allowed_types))
                        raise ParseError(
                            f"Error: Only these SQL commands are allowed: {allowed_commands_str}. Found statement type: {stmt.key}"
                        )

        except ParseError as e:
            allowed_commands_upper = [cmd.upper() for cmd in config.security.allowed_sql_commands]
            allowed_commands_str = "/".join(sorted(allowed_commands_upper))
            
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: Only {allowed_commands_str} queries are allowed. {str(e)}",
                )
            ]

        # Choose appropriate database operations based on transaction requirements
        if use_transaction:
            async with get_transactional_database_ops(request_ctx) as db_ops:
                return await _execute_query_with_output(
                    query, db_ops, output_handler, output_mode, file_format, 
                    location, filename, request_ctx, database, schema, 
                    limit_rows, use_transaction, auto_commit
                )
        else:
            async with get_isolated_database_ops(request_ctx) as db_ops:
                return await _execute_query_with_output(
                    query, db_ops, output_handler, output_mode, file_format,
                    location, filename, request_ctx, database, schema,
                    limit_rows, use_transaction, auto_commit
                )

    except Exception as e:
        logger.error(f"Error executing query: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error executing query: {str(e)}")
        ]


async def _execute_query_with_output(
    query: str,
    db_ops,
    output_handler: ResultOutputHandler,
    output_mode: str,
    file_format: str,
    location: Optional[str],
    filename: Optional[str],
    request_ctx: RequestContext,
    database: Optional[str],
    schema: Optional[str],
    limit_rows: int,
    use_transaction: bool,
    auto_commit: bool
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Execute query with smart output handling."""
    
    # Set database and schema context
    if database:
        await db_ops.use_database_isolated(database)
    if schema:
        await db_ops.use_schema_isolated(schema)

    # Get current context for display
    current_db, current_schema = await db_ops.get_current_context()

    # For file output, don't add automatic LIMIT - let token estimation decide
    original_query = query
    
    # Only add LIMIT for screen output or when explicitly requested
    if output_mode == "screen" or (output_mode == "auto" and "LIMIT " not in query.upper()):
        query_upper = query.strip().upper()
        if (query_upper.startswith("SELECT") or query_upper.startswith("WITH")) and "LIMIT " not in query_upper:
            query = query.rstrip().rstrip(";")
            query = f"{query} LIMIT {limit_rows};"

    # Determine output strategy
    use_file, reasoning = await output_handler.estimator.should_use_file_output(
        original_query, db_ops, 
        force_output=None if output_mode == "auto" else output_mode
    )

    if use_file:
        # Generate filename if needed
        if not filename and output_handler.config.auto_generate_filename:
            filename = output_handler.generate_filename(file_format, original_query)

        # Write to file
        try:
            result = await output_handler.write_to_file(
                original_query, db_ops, file_format, location, filename
            )
            
            # Build parameter sources for feedback
            param_sources = []
            if filename:
                param_sources.append(f"Filename: {result['file_path'].split('/')[-1]} (specified)")
            else:
                param_sources.append(f"Filename: {result['file_path'].split('/')[-1]} (auto-generated)")
            
            param_sources.append(f"Location: {location}")
            param_sources.append(f"Format: {file_format}")

            response_text = f"""Query executed and saved to file.

**Configuration:** {output_handler.config.model_name} (limit: {output_handler.config.model_token_limit:,} tokens)
**Output Decision:** {reasoning['reason']}

**Parameters Used:**
{chr(10).join('- ' + p for p in param_sources)}

**File Details:**
- Full Path: `{result['file_path']}`
- Format: {result['format'].upper()}
- Rows: {result['rows_written']:,}
- Columns: {result['columns_written']}
- Size: {result['file_size_readable']} ({result['file_size_bytes']:,} bytes)

**Database Context:** {current_db}.{current_schema}
**Request ID:** {request_ctx.request_id}

File saved successfully. You can read this file for analysis."""

            return [mcp_types.TextContent(type="text", text=response_text)]
            
        except Exception as e:
            logger.error(f"Error writing to file: {e}")
            return [
                mcp_types.TextContent(
                    type="text", 
                    text=f"Error writing query results to file: {str(e)}"
                )
            ]

    else:
        # Screen output (existing logic)
        if use_transaction:
            rows, column_names = await db_ops.execute_with_transaction(query, auto_commit)
        else:
            rows, column_names = await db_ops.execute_query_isolated(query)

        if rows:
            # Format results
            result = f"## Query Results (Database: {current_db}, Schema: {current_schema})\n\n"
            result += f"Request ID: {request_ctx.request_id}\n"
            result += f"Model: {output_handler.config.model_name} (limit: {output_handler.config.model_token_limit:,} tokens)\n"
            result += f"Output Decision: {reasoning['reason']}\n"
            if use_transaction:
                result += f"Transaction Mode: {'Auto-commit' if auto_commit else 'Explicit'}\n"
            result += f"Showing {len(rows)} row{'s' if len(rows) != 1 else ''}\n\n"
            result += f"```sql\n{query}\n```\n\n"

            # Create header row
            result += "| " + " | ".join(column_names) + " |\n"
            result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

            # Add data rows with truncation
            for row in rows:
                formatted_values = []
                for val in row:
                    if val is None:
                        formatted_values.append("NULL")
                    else:
                        val_str = str(val).replace("|", "\\|")
                        if len(val_str) > 200:
                            val_str = val_str[:197] + "..."
                        formatted_values.append(val_str)
                result += "| " + " | ".join(formatted_values) + " |\n"

            return [mcp_types.TextContent(type="text", text=result)]
        else:
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Query completed successfully but returned no results.",
                )
            ]


# Function to run the server with stdio interface
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""

    async def run() -> None:
        # Set up contextual logging first
        setup_server_logging()
        
        # Initialize async infrastructure
        await initialize_async_infrastructure()
        
        server = create_server()

        # Register all the Snowflake tools
        @server.call_tool()
        async def call_tool(
            name: str, arguments: Optional[Dict[str, Any]] = None
        ) -> Sequence[
            Union[
                mcp_types.TextContent,
                mcp_types.ImageContent,
                mcp_types.EmbeddedResource,
            ]
        ]:
            if name == "list_databases":
                return await handle_list_databases(name, arguments)
            elif name == "list_views":
                return await handle_list_views(name, arguments)
            elif name == "describe_view":
                return await handle_describe_view(name, arguments)
            elif name == "query_view":
                return await handle_query_view(name, arguments)
            elif name == "execute_query":
                return await handle_execute_query(name, arguments)
            else:
                return [
                    mcp_types.TextContent(type="text", text=f"Unknown tool: {name}")
                ]

        # Create tool definitions for all Snowflake tools
        @server.list_tools()
        async def list_tools() -> List[mcp_types.Tool]:
            """
            AI Usage Guide for Snowflake MCP Server:
            
            SMART OUTPUT HANDLING:
            - Use output='auto' (default) for intelligent routing based on result size
            - Server automatically saves large results to files when they exceed token limits:
              * Claude Sonnet: ~140K tokens safe (200K limit)
              * GPT-4: ~70K tokens safe (100K limit) 
              * Gemini Pro: ~700K tokens safe (1M limit)
            
            WHEN TO USE EACH OUTPUT MODE:
            - output='auto': Let server decide (recommended for all queries)
            - output='screen': Small results, want inline display
            - output='file': Large datasets, reports, data exports
            
            PARAMETER PRECEDENCE (AI parameters always override environment):
            - Specify only parameters you want to control
            - Leave others blank to use environment defaults
            - Example: {"query": "...", "format": "json"} uses env location/filename
            """
            return [
                mcp_types.Tool(
                    name="list_databases",
                    description="List all accessible Snowflake databases",
                    inputSchema={"type": "object", "properties": {}, "required": []},
                ),
                mcp_types.Tool(
                    name="list_views",
                    description="List all views in a specified database and schema",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "database": {
                                "type": "string",
                                "description": "The database name (required)",
                            },
                            "schema": {
                                "type": "string",
                                "description": "The schema name (optional, will use current schema if not provided)",
                            },
                        },
                        "required": ["database"],
                    },
                ),
                mcp_types.Tool(
                    name="describe_view",
                    description="Get detailed information about a specific view including columns and SQL definition",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "database": {
                                "type": "string",
                                "description": "The database name (required)",
                            },
                            "schema": {
                                "type": "string",
                                "description": "The schema name (optional, will use current schema if not provided)",
                            },
                            "view_name": {
                                "type": "string",
                                "description": "The name of the view to describe (required)",
                            },
                        },
                        "required": ["database", "view_name"],
                    },
                ),
                mcp_types.Tool(
                    name="query_view",
                    description="Query data from a view with an optional row limit",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "database": {
                                "type": "string",
                                "description": "The database name (required)",
                            },
                            "schema": {
                                "type": "string",
                                "description": "The schema name (optional, will use current schema if not provided)",
                            },
                            "view_name": {
                                "type": "string",
                                "description": "The name of the view to query (required)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of rows to return (default: 10)",
                            },
                        },
                        "required": ["database", "view_name"],
                    },
                ),
                mcp_types.Tool(
                    name="execute_query",
                    description="Execute SQL queries with intelligent output handling: automatically saves large results to files when they exceed AI token limits, returns smaller results inline. Supports CSV/JSON formats with customizable parameters.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The SQL query to execute (allowed commands are configurable via ALLOWED_SQL_COMMANDS environment variable)",
                            },
                            "database": {
                                "type": "string",
                                "description": "The database to use (optional)",
                            },
                            "schema": {
                                "type": "string",
                                "description": "The schema to use (optional)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of rows to return (default: 100)",
                            },
                            "use_transaction": {
                                "type": "boolean",
                                "description": "Enable transaction boundary management for this query (default: false)",
                            },
                            "auto_commit": {
                                "type": "boolean", 
                                "description": "Auto-commit transaction when use_transaction is true (default: true)",
                            },
                            "output": {
                                "type": "string",
                                "enum": ["auto", "screen", "file"],
                                "description": "Output mode: 'auto' (recommended - server intelligently decides based on result size vs model token limits), 'screen' (force inline display for small results), 'file' (force file output for large datasets or when you need to preserve results)"
                            },
                            "format": {
                                "type": "string", 
                                "enum": ["csv", "json"],
                                "description": "File format: 'csv' (human-readable, Excel-compatible), 'json' (structured data, programmatic analysis). Only used when output is 'file'"
                            },
                            "location": {
                                "type": "string",
                                "description": "Output directory path (relative to server root or absolute). Leave blank to use default from environment. Only used when output is 'file'"
                            },
                            "filename": {
                                "type": "string",
                                "description": "Custom output filename (without extension). Leave blank for auto-generated timestamp-based names. Extension auto-added based on format"
                            },
                        },
                        "required": ["query"],
                    },
                ),
            ]

        init_options = server.create_initialization_options()

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)

    anyio.run(run)


def run_http_server() -> None:
    """Run the HTTP/WebSocket MCP server."""
    # Parse command line arguments for host and port
    import sys

    from snowflake_mcp_server.transports.http_server import (
        run_http_server as _run_http_server,
    )
    
    host = "0.0.0.0"
    port = 8000
    
    # Simple argument parsing
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
    
    logger.info(f"Starting Snowflake MCP HTTP server on {host}:{port}")
    _run_http_server(host, port)


async def get_available_tools() -> List[Dict[str, Any]]:
    """Get list of available MCP tools for HTTP API."""
    return [
        {
            "name": "list_databases",
            "description": "List all accessible Snowflake databases",
            "parameters": {}
        },
        {
            "name": "list_views", 
            "description": "List all views in a specified database and schema",
            "parameters": {
                "database": {"type": "string", "required": True},
                "schema": {"type": "string", "required": False}
            }
        },
        {
            "name": "describe_view",
            "description": "Get detailed information about a specific view including columns and SQL definition",
            "parameters": {
                "database": {"type": "string", "required": True},
                "view_name": {"type": "string", "required": True},
                "schema": {"type": "string", "required": False}
            }
        },
        {
            "name": "query_view",
            "description": "Query data from a specific view with optional limit",
            "parameters": {
                "database": {"type": "string", "required": True},
                "view_name": {"type": "string", "required": True},
                "schema": {"type": "string", "required": False},
                "limit": {"type": "integer", "required": False}
            }
        },
        {
            "name": "execute_query",
            "description": "Execute a SQL query against Snowflake with optional transaction control",
            "parameters": {
                "query": {"type": "string", "required": True, "description": "SQL query (allowed commands configurable via ALLOWED_SQL_COMMANDS)"},
                "database": {"type": "string", "required": False},
                "schema": {"type": "string", "required": False},
                "limit": {"type": "integer", "required": False},
                "use_transaction": {"type": "boolean", "required": False},
                "auto_commit": {"type": "boolean", "required": False},
                "output": {"type": "string", "required": False, "description": "Output mode: auto, screen, or file"},
                "format": {"type": "string", "required": False, "description": "File format: csv or json"},
                "location": {"type": "string", "required": False, "description": "Output directory path"},
                "filename": {"type": "string", "required": False, "description": "Output filename"}
            }
        }
    ]
