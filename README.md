# MCP Server for Snowflake

A Model Context Protocol (MCP) server for performing read-only operations against Snowflake databases. This tool enables Claude to securely query Snowflake data without modifying any information.

## Features

- Secure connection to Snowflake using service account authentication with private key
- MCP-compatible handlers for querying Snowflake data
- Read-only operations with security checks to prevent data modification
- Support for Python 3.12+
- Stdio-based MCP server for easy integration with Claude Desktop

## Available Tools

The server provides the following tools for querying Snowflake:

- **list_databases**: List all accessible Snowflake databases
- **list_views**: List all views in a specified database and schema
- **describe_view**: Get detailed information about a specific view including columns and SQL definition
- **query_view**: Query data from a view with an optional row limit
- **execute_query**: Execute custom read-only SQL queries (SELECT only) with results formatted as markdown tables

## Installation

### Prerequisites

- Python 3.12 or higher
- A Snowflake account with a configured service account (username + private key)
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Steps

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/mcp-server-snowflake.git
   cd mcp-server-snowflake
   ```

2. Install the package:
   ```
   uv pip install -e .
   ```

3. Create a `.env` file based on `.env.example` with your Snowflake credentials:
   ```
   SNOWFLAKE_ACCOUNT=youraccount.region
   SNOWFLAKE_USER=your_service_account_username
   SNOWFLAKE_PRIVATE_KEY_PATH=/absolute/path/to/your/rsa_key.p8
   SNOWFLAKE_WAREHOUSE=your_warehouse
   SNOWFLAKE_DATABASE=your_database
   SNOWFLAKE_SCHEMA=your_schema
   SNOWFLAKE_ROLE=your_role
   ```

## Usage

### Running with uv

After installing the package, you can run the server directly with:

```
uv run snowflake-mcp
```

This will start the stdio-based MCP server, which can be connected to Claude Desktop or any MCP client that supports stdio communication.

### Claude Desktop Integration

1. In Claude Desktop, go to Settings → MCP Servers
2. Add a new server with the full path to your uv executable:
   ```
   /path/to/uv run snowflake-mcp
   ```
3. You can find your uv path by running `which uv` in your terminal
4. Save the server configuration

### Example Queries

When using with Claude, you can ask questions like:

- "Can you list all the databases in my Snowflake account?"
- "List all views in the MARKETING database"
- "Describe the structure of the CUSTOMER_ANALYTICS view in the SALES database"
- "Show me sample data from the REVENUE_BY_REGION view in the FINANCE database"
- "Run this SQL query: SELECT customer_id, SUM(order_total) as total_spend FROM SALES.ORDERS GROUP BY customer_id ORDER BY total_spend DESC LIMIT 10"
- "Query the MARKETING database to find the top 5 performing campaigns by conversion rate"

## Security Considerations

This server:
- Enforces read-only operations (only SELECT statements are allowed)
- Automatically adds LIMIT clauses to prevent large result sets
- Uses service account authentication for secure connections
- Validates inputs to prevent SQL injection

⚠️ **Important**: Keep your `.env` file secure and never commit it to version control. The `.gitignore` file is configured to exclude it.

## Development

### Environment Setup

```
# Create a virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies
uv pip install -e ".[dev]"
```

### Running Tests

```
pytest
```

### Static Type Checking

```
mypy mcp_server_snowflake/
```

### Linting

```
ruff check .
```

### Formatting

```
ruff format .
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Technical Details

This project uses:
- [Snowflake Connector Python](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector) for connecting to Snowflake
- [MCP (Model Context Protocol)](https://github.com/anthropics/anthropic-cookbook/tree/main/mcp) for interacting with Claude
- [Pydantic](https://docs.pydantic.dev/) for data validation
- [python-dotenv](https://github.com/theskumar/python-dotenv) for environment variable management