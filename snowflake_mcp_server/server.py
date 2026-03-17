"""FastMCP server for Snowflake read-only operations.

This module defines the FastMCP server instance and all tool definitions.
Tools use @mcp.tool decorators with type hints for automatic schema generation.
Snowflake connections are injected via request_context and isolated DB ops.
"""

import logging
from typing import Annotated, Optional

import sqlglot
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlglot.errors import ParseError

from snowflake_mcp_server.config import get_config
from snowflake_mcp_server.utils.async_database import (
    get_isolated_database_ops,
    get_transactional_database_ops,
)
from snowflake_mcp_server.utils.output_handler import ResultOutputHandler
from snowflake_mcp_server.utils.request_context import request_context

logger = logging.getLogger(__name__)

# --- FastMCP Server Instance ---

mcp = FastMCP(
    "snowflake-mcp-server",
    instructions=(
        "MCP server for performing read-only operations against Snowflake. "
        "All tools are read-only and will not modify any data."
    ),
)


# --- Health endpoint ---

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for PM2 monitoring."""
    import time

    from starlette.responses import JSONResponse

    return JSONResponse({
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    })


# --- Tool annotations (all tools are read-only) ---

READONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
}


# --- Tool Definitions ---

@mcp.tool(
    annotations=READONLY_ANNOTATIONS,
    description="List all accessible Snowflake databases",
)
async def list_databases() -> str:
    """List all accessible Snowflake databases."""
    try:
        async with request_context("list_databases", {}, "fastmcp") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                results, _ = await db_ops.execute_query_isolated("SHOW DATABASES")
                databases = [row[1] for row in results]
                return "Available Snowflake databases:\n" + "\n".join(databases)
    except Exception as e:
        logger.error(f"Error querying databases: {e}")
        raise ToolError(f"Error querying databases: {e}")


@mcp.tool(
    annotations=READONLY_ANNOTATIONS,
    description="List all views in a specified database and schema",
)
async def list_views(
    database: Annotated[str, Field(description="The database name")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (optional, uses current schema if not provided)"),
    ] = None,
) -> str:
    """List all views in a specified database and schema."""
    try:
        async with request_context("list_views", {"database": database}, "fastmcp") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                await db_ops.use_database_isolated(database)

                if schema:
                    await db_ops.use_schema_isolated(schema)
                else:
                    _, current_schema = await db_ops.get_current_context()
                    schema = current_schema

                results, _ = await db_ops.execute_query_isolated(
                    f"SHOW VIEWS IN {database}.{schema}"
                )

                views = []
                for row in results:
                    view_name = row[1]
                    created_on = row[5]
                    views.append(f"{view_name} (created: {created_on})")

                if views:
                    return f"Views in {database}.{schema}:\n" + "\n".join(views)
                else:
                    return f"No views found in {database}.{schema}"
    except Exception as e:
        logger.error(f"Error listing views: {e}")
        raise ToolError(f"Error listing views: {e}")


@mcp.tool(
    annotations=READONLY_ANNOTATIONS,
    description="Get detailed information about a specific view including columns and SQL definition",
)
async def describe_view(
    database: Annotated[str, Field(description="The database name")],
    view_name: Annotated[str, Field(description="The name of the view to describe")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (optional)"),
    ] = None,
) -> str:
    """Get detailed information about a specific view."""
    try:
        async with request_context(
            "describe_view", {"database": database, "view_name": view_name}, "fastmcp"
        ) as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                await db_ops.use_database_isolated(database)

                if schema:
                    await db_ops.use_schema_isolated(schema)
                    full_view_name = f"{database}.{schema}.{view_name}"
                else:
                    _, current_schema = await db_ops.get_current_context()
                    if current_schema and current_schema != "Unknown":
                        schema = current_schema
                        full_view_name = f"{database}.{schema}.{view_name}"
                    else:
                        raise ToolError("Could not determine current schema")

                describe_results, _ = await db_ops.execute_query_isolated(
                    f"DESCRIBE VIEW {full_view_name}"
                )

                columns = []
                for row in describe_results:
                    col_name = row[0]
                    col_type = row[1]
                    col_null = "NULL" if row[3] == "Y" else "NOT NULL"
                    columns.append(f"{col_name} : {col_type} {col_null}")

                ddl_results, _ = await db_ops.execute_query_isolated(
                    f"SELECT GET_DDL('VIEW', '{full_view_name}')"
                )
                view_ddl = (
                    ddl_results[0][0]
                    if ddl_results and ddl_results[0]
                    else "Definition not available"
                )

                if columns:
                    result = f"## View: {full_view_name}\n\n"
                    result += "### Columns:\n"
                    for col in columns:
                        result += f"- {col}\n"
                    result += f"\n### View Definition:\n```sql\n{view_ddl}\n```"
                    return result
                else:
                    return f"View {full_view_name} not found or you don't have permission."
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error describing view: {e}")
        raise ToolError(f"Error describing view: {e}")


@mcp.tool(
    annotations=READONLY_ANNOTATIONS,
    description="Query data from a view with an optional row limit",
)
async def query_view(
    database: Annotated[str, Field(description="The database name")],
    view_name: Annotated[str, Field(description="The name of the view to query")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (optional)"),
    ] = None,
    limit: Annotated[int, Field(description="Maximum number of rows to return")] = 10,
) -> str:
    """Query data from a specific view with optional limit."""
    try:
        async with request_context(
            "query_view", {"database": database, "view_name": view_name}, "fastmcp"
        ) as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                await db_ops.use_database_isolated(database)

                if schema:
                    await db_ops.use_schema_isolated(schema)
                    full_view_name = f"{database}.{schema}.{view_name}"
                else:
                    _, current_schema = await db_ops.get_current_context()
                    if current_schema and current_schema != "Unknown":
                        schema = current_schema
                        full_view_name = f"{database}.{schema}.{view_name}"
                    else:
                        raise ToolError("Could not determine current schema")

                rows, column_names = await db_ops.execute_query_limited(
                    f"SELECT * FROM {full_view_name}", limit
                )

                if rows:
                    result = f"## Data from {full_view_name} (Showing {len(rows)} rows)\n\n"
                    result += "| " + " | ".join(column_names) + " |\n"
                    result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

                    for row in rows:
                        formatted = []
                        for val in row:
                            if val is None:
                                formatted.append("NULL")
                            else:
                                val_str = str(val).replace("|", "\\|")
                                if len(val_str) > 200:
                                    val_str = val_str[:197] + "..."
                                formatted.append(val_str)
                        result += "| " + " | ".join(formatted) + " |\n"

                    return result
                else:
                    return f"No data found in view {full_view_name}."
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error querying view: {e}")
        raise ToolError(f"Error querying view: {e}")


@mcp.tool(
    annotations=READONLY_ANNOTATIONS,
    timeout=300.0,
    description=(
        "Execute SQL queries with intelligent output handling: automatically saves "
        "large results to files when they exceed AI token limits, returns smaller "
        "results inline. Supports CSV/JSON formats."
    ),
)
async def execute_query(
    query: Annotated[str, Field(description="The SQL query to execute")],
    database: Annotated[Optional[str], Field(description="The database to use")] = None,
    schema: Annotated[Optional[str], Field(description="The schema to use")] = None,
    limit: Annotated[int, Field(description="Maximum number of rows to return")] = 100,
    use_transaction: Annotated[
        bool, Field(description="Enable transaction boundary management")
    ] = False,
    auto_commit: Annotated[
        bool, Field(description="Auto-commit transaction when use_transaction is true")
    ] = True,
    output: Annotated[
        Optional[str],
        Field(description="Output mode: 'auto' (recommended), 'screen', or 'file'"),
    ] = None,
    format: Annotated[
        Optional[str],
        Field(description="File format: 'csv' or 'json'. Only used when output is 'file'"),
    ] = None,
    location: Annotated[
        Optional[str],
        Field(description="Output directory path. Only used when output is 'file'"),
    ] = None,
    filename: Annotated[
        Optional[str], Field(description="Custom output filename")
    ] = None,
) -> str:
    """Execute SQL queries with intelligent output handling."""
    config = get_config()
    output_config = config.output

    # Apply defaults from config if AI didn't specify
    if output is None:
        output = output_config.default_output
    if format is None:
        format = output_config.default_file_format
    if location is None:
        location = output_config.default_output_dir

    # Initialize output handler
    output_handler = ResultOutputHandler(output_config)

    # Validate output parameters
    is_valid, error_msg = output_handler.validate_output_parameters(
        output, format, location, filename
    )
    if not is_valid:
        raise ToolError(f"Invalid output parameters: {error_msg}")

    # Validate SQL commands
    try:
        allowed_types = set(config.security.allowed_sql_commands)
        parsed_statements = sqlglot.parse(query, dialect="snowflake")

        if not parsed_statements:
            raise ParseError("Could not parse SQL query")

        for stmt in parsed_statements:
            if stmt is not None and hasattr(stmt, "key") and stmt.key:
                stmt_key = stmt.key.lower()

                if stmt_key == "command" and hasattr(stmt, "this") and stmt.this:
                    command_text = str(stmt.this).strip().upper()
                    if command_text.startswith("CALL"):
                        stmt_key = "call"
                    elif command_text.startswith("SHOW"):
                        stmt_key = "show"
                    elif command_text.startswith(("DESCRIBE", "DESC")):
                        stmt_key = "describe"

                if stmt_key not in allowed_types:
                    allowed_str = ", ".join(sorted(allowed_types))
                    raise ToolError(
                        f"Only these SQL commands are allowed: {allowed_str}. "
                        f"Found: {stmt.key}"
                    )
    except ParseError as e:
        allowed_upper = [cmd.upper() for cmd in config.security.allowed_sql_commands]
        raise ToolError(
            f"Only {'/'.join(sorted(allowed_upper))} queries are allowed. {e}"
        )

    # Execute query
    try:
        async with request_context("execute_query", {"query": query}, "fastmcp") as ctx:
            if use_transaction:
                async with get_transactional_database_ops(ctx) as db_ops:
                    return await _execute_with_output(
                        query, db_ops, output_handler, output, format,
                        location, filename, ctx, database, schema,
                        limit, use_transaction, auto_commit,
                    )
            else:
                async with get_isolated_database_ops(ctx) as db_ops:
                    return await _execute_with_output(
                        query, db_ops, output_handler, output, format,
                        location, filename, ctx, database, schema,
                        limit, use_transaction, auto_commit,
                    )
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        raise ToolError(f"Error executing query: {e}")


async def _execute_with_output(
    query, db_ops, output_handler, output_mode, file_format,
    location, filename, request_ctx, database, schema,
    limit_rows, use_transaction, auto_commit,
) -> str:
    """Execute query with smart output handling. Returns formatted string."""
    if database:
        await db_ops.use_database_isolated(database)
    if schema:
        await db_ops.use_schema_isolated(schema)

    current_db, current_schema = await db_ops.get_current_context()

    original_query = query
    if output_mode == "screen" or (output_mode == "auto" and "LIMIT " not in query.upper()):
        query_upper = query.strip().upper()
        if (query_upper.startswith("SELECT") or query_upper.startswith("WITH")) and "LIMIT " not in query_upper:
            query = query.rstrip().rstrip(";")
            query = f"{query} LIMIT {limit_rows};"

    use_file, reasoning = await output_handler.estimator.should_use_file_output(
        original_query, db_ops,
        force_output=None if output_mode == "auto" else output_mode,
    )

    if use_file:
        if not filename and output_handler.config.auto_generate_filename:
            filename = output_handler.generate_filename(file_format, original_query)

        result = await output_handler.write_to_file(
            original_query, db_ops, file_format, location, filename
        )

        return (
            f"Query executed and saved to file.\n\n"
            f"**Output Decision:** {reasoning['reason']}\n\n"
            f"**File Details:**\n"
            f"- Full Path: `{result['file_path']}`\n"
            f"- Format: {result['format'].upper()}\n"
            f"- Rows: {result['rows_written']:,}\n"
            f"- Columns: {result['columns_written']}\n"
            f"- Size: {result['file_size_readable']} ({result['file_size_bytes']:,} bytes)\n\n"
            f"**Database Context:** {current_db}.{current_schema}\n\n"
            f"File saved successfully. You can read this file for analysis."
        )
    else:
        if use_transaction:
            rows, column_names = await db_ops.execute_with_transaction(query, auto_commit)
        else:
            rows, column_names = await db_ops.execute_query_isolated(query)

        if rows:
            result = f"## Query Results (Database: {current_db}, Schema: {current_schema})\n\n"
            result += f"Output Decision: {reasoning['reason']}\n"
            result += f"Showing {len(rows)} row{'s' if len(rows) != 1 else ''}\n\n"
            result += f"```sql\n{query}\n```\n\n"
            result += "| " + " | ".join(column_names) + " |\n"
            result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

            for row in rows:
                formatted = []
                for val in row:
                    if val is None:
                        formatted.append("NULL")
                    else:
                        val_str = str(val).replace("|", "\\|")
                        if len(val_str) > 200:
                            val_str = val_str[:197] + "..."
                        formatted.append(val_str)
                result += "| " + " | ".join(formatted) + " |\n"

            return result
        else:
            return "Query completed successfully but returned no results."
