"""MCP server implementation for Snowflake.

This module provides a Model Context Protocol (MCP) server that allows Claude
to perform read-only operations against Snowflake databases. It connects to
Snowflake using either service account authentication with a private key or
external browser authentication. It exposes various tools for querying database
metadata and data, including support for multi-view and multi-database queries.

The server is designed to be used with Claude Desktop as an MCP server, providing
Claude with secure, controlled access to Snowflake data for analysis and reporting.
"""

import os
from typing import Any, Dict, List, Optional, Sequence, Union

import anyio
import mcp.types as mcp_types
import sqlglot
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from sqlglot.errors import ParseError

from mcp_server_snowflake.utils.snowflake_conn import (
    AuthType,
    SnowflakeConfig,
    connection_manager,
)

# Load environment variables from .env file
load_dotenv()


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
        name="mcp-server-snowflake",
        version="0.2.0",
        instructions="MCP server for performing read-only operations against "
        "Snowflake.",
    )

    return server


# Snowflake query handler functions
async def handle_list_databases(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to list all accessible Snowflake databases."""
    try:
        # Get Snowflake connection from connection manager
        conn = connection_manager.get_connection()

        # Execute query
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES")

        # Process results
        databases = []
        for row in cursor:
            databases.append(row[1])  # Database name is in the second column

        cursor.close()
        # Don't close the connection, just the cursor

        # Return formatted content
        return [
            mcp_types.TextContent(
                type="text",
                text="Available Snowflake databases:\n" + "\n".join(databases),
            )
        ]

    except Exception as e:
        return [
            mcp_types.TextContent(
                type="text", text=f"Error querying databases: {str(e)}"
            )
        ]


async def handle_list_views(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to list views in a specified database and schema."""
    try:
        # Get Snowflake connection from connection manager
        conn = connection_manager.get_connection()

        # Extract arguments
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None

        if not database:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: database parameter is required"
                )
            ]

        # Use the provided database and schema, or use default schema
        if database:
            conn.cursor().execute(f"USE DATABASE {database}")
        if schema:
            conn.cursor().execute(f"USE SCHEMA {schema}")
        else:
            # Get the current schema
            cursor = conn.cursor()
            cursor.execute("SELECT CURRENT_SCHEMA()")
            schema_result = cursor.fetchone()
            if schema_result:
                schema = schema_result[0]
            else:
                return [
                    mcp_types.TextContent(
                        type="text", text="Error: Could not determine current schema"
                    )
                ]

        # Execute query to list views
        cursor = conn.cursor()
        cursor.execute(f"SHOW VIEWS IN {database}.{schema}")

        # Process results
        views = []
        for row in cursor:
            view_name = row[1]  # View name is in the second column
            created_on = row[5]  # Creation date
            views.append(f"{view_name} (created: {created_on})")

        cursor.close()
        # Don't close the connection, just the cursor

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
        return [
            mcp_types.TextContent(type="text", text=f"Error listing views: {str(e)}")
        ]


async def handle_describe_view(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to describe the structure of a view."""
    try:
        # Get Snowflake connection from connection manager
        conn = connection_manager.get_connection()

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

        # Use the provided schema or use default schema
        if schema:
            full_view_name = f"{database}.{schema}.{view_name}"
        else:
            # Get the current schema
            cursor = conn.cursor()
            cursor.execute("SELECT CURRENT_SCHEMA()")
            schema_result = cursor.fetchone()
            if schema_result:
                schema = schema_result[0]
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                return [
                    mcp_types.TextContent(
                        type="text", text="Error: Could not determine current schema"
                    )
                ]

        # Execute query to describe view
        cursor = conn.cursor()
        cursor.execute(f"DESCRIBE VIEW {full_view_name}")

        # Process results
        columns = []
        for row in cursor:
            col_name = row[0]
            col_type = row[1]
            col_null = "NULL" if row[3] == "Y" else "NOT NULL"
            columns.append(f"{col_name} : {col_type} {col_null}")

        # Get view definition
        cursor.execute(f"SELECT GET_DDL('VIEW', '{full_view_name}')")
        view_ddl_result = cursor.fetchone()
        view_ddl = view_ddl_result[0] if view_ddl_result else "Definition not available"

        cursor.close()
        # Don't close the connection, just the cursor

        if columns:
            result = f"## View: {full_view_name}\n\n"
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
        return [
            mcp_types.TextContent(type="text", text=f"Error describing view: {str(e)}")
        ]


async def handle_query_view(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to query data from a view with optional limit."""
    try:
        # Get Snowflake connection from connection manager
        conn = connection_manager.get_connection()

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

        # Use the provided schema or use default schema
        if schema:
            full_view_name = f"{database}.{schema}.{view_name}"
        else:
            # Get the current schema
            cursor = conn.cursor()
            cursor.execute("SELECT CURRENT_SCHEMA()")
            schema_result = cursor.fetchone()
            if schema_result:
                schema = schema_result[0]
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                return [
                    mcp_types.TextContent(
                        type="text", text="Error: Could not determine current schema"
                    )
                ]

        # Execute query to get data from view
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {full_view_name} LIMIT {limit}")

        # Get column names
        column_names = (
            [col[0] for col in cursor.description] if cursor.description else []
        )

        # Process results
        rows = cursor.fetchall()

        cursor.close()
        # Don't close the connection, just the cursor

        if rows:
            # Format the results as a markdown table
            result = f"## Data from {full_view_name} (Showing {len(rows)} rows)\n\n"

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
                        formatted_values.append(str(val).replace("|", "\\|"))
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
        return [
            mcp_types.TextContent(type="text", text=f"Error querying view: {str(e)}")
        ]


async def handle_execute_query(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[
    Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]
]:
    """Tool handler to execute read-only SQL queries against Snowflake."""
    try:
        # Get Snowflake connection from connection manager
        conn = connection_manager.get_connection()

        # Extract arguments
        query = arguments.get("query") if arguments else None
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        limit_rows = (
            int(arguments.get("limit", 100))
            if arguments and arguments.get("limit") is not None
            else 100
        )  # Default limit to 100 rows

        if not query:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: query parameter is required"
                )
            ]

        # Validate that the query is read-only
        try:
            parsed_statements = sqlglot.parse(query, dialect="snowflake")
            read_only_types = {"select", "show", "describe", "explain", "with"}

            if not parsed_statements:
                raise ParseError("Error: Could not parse SQL query")

            for stmt in parsed_statements:
                if (
                    stmt is not None
                    and hasattr(stmt, "key")
                    and stmt.key
                    and stmt.key.lower() not in read_only_types
                ):
                    raise ParseError(
                        f"Error: Only read-only queries are allowed. Found statement type: {stmt.key}"
                    )

        except ParseError as e:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: Only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH queries are allowed for security reasons. {str(e)}",
                )
            ]

        # Use the specified database and schema if provided
        if database:
            conn.cursor().execute(f"USE DATABASE {database}")
        if schema:
            conn.cursor().execute(f"USE SCHEMA {schema}")

        # Extract database and schema context info for logging/display
        context_cursor = conn.cursor()
        context_cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
        context_result = context_cursor.fetchone()
        if context_result:
            current_db, current_schema = context_result
        else:
            current_db, current_schema = "Unknown", "Unknown"
        context_cursor.close()

        # Ensure the query has a LIMIT clause to prevent large result sets
        # Parse the query to check if it already has a LIMIT
        if "LIMIT " not in query.upper():
            # Remove any trailing semicolon before adding the LIMIT clause
            query = query.rstrip().rstrip(";")
            query = f"{query} LIMIT {limit_rows};"

        # Execute the query
        cursor = conn.cursor()
        cursor.execute(query)

        # Get column names and types
        column_names = (
            [col[0] for col in cursor.description] if cursor.description else []
        )

        # Fetch only up to limit_rows
        rows = cursor.fetchmany(limit_rows)
        row_count = len(rows) if rows else 0

        cursor.close()
        # Don't close the connection, just the cursor

        if rows:
            # Format the results as a markdown table
            result = f"## Query Results (Database: {current_db}, Schema: {current_schema})\n\n"
            result += f"Showing {row_count} row{'s' if row_count != 1 else ''}\n\n"
            result += f"```sql\n{query}\n```\n\n"

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
                        # Truncate very long values to prevent huge tables
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
                    text=f"Query executed successfully in {current_db}.{current_schema}, but returned no results.",
                )
            ]

    except Exception as e:
        return [
            mcp_types.TextContent(type="text", text=f"Error executing query: {str(e)}")
        ]


# Function to run the server with stdio interface
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""

    async def run() -> None:
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
                    description="Execute a read-only SQL query against Snowflake",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The SQL query to execute (supports SELECT, SHOW, DESCRIBE, EXPLAIN, and WITH statements)",
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
                        },
                        "required": ["query"],
                    },
                ),
            ]

        init_options = server.create_initialization_options()

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)

    anyio.run(run)
