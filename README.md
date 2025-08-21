# MCP Server for Snowflake

A Model Context Protocol (MCP) server for performing read-only operations against Snowflake databases. This tool enables Claude to securely query Snowflake data without modifying any information.

## Features

- Flexible authentication to Snowflake using either:
  - Service account authentication with private key
  - External browser authentication for interactive sessions
- Connection pooling with automatic background refresh to maintain persistent connections
- Support for querying multiple views and databases in a single session
- Configurable SQL statement types (default: SELECT, SHOW, DESCRIBE, EXPLAIN, WITH, UNION; configurable via ALLOWED_SQL_COMMANDS)
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
- **execute_query**: Execute custom SQL queries with configurable command restrictions (default: SELECT, SHOW, DESCRIBE, EXPLAIN, WITH, UNION) with intelligent output handling:
  - **Screen output**: Results formatted as markdown tables for smaller datasets
  - **File output**: Automatic CSV/JSON file generation for large datasets that exceed AI token limits
  - **Smart decision making**: Token-aware output routing based on result size and model capabilities

## Installation

### Prerequisites

- Python 3.12 or higher
- A Snowflake account with either:
  - A configured service account (username + private key), or
  - A regular user account for browser-based authentication
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Steps

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/snowflake-mcp-server.git
   cd snowflake-mcp-server
   ```

2. Install the package:
   ```
   uv pip install -e .
   ```

3. Create a `.env` file with your Snowflake credentials:

   Choose one of the provided example files based on your preferred authentication method:

   **For private key authentication**:
   ```
   cp .env.private_key.example .env
   ```
   Then edit the `.env` file to set your Snowflake account details and path to your private key.

   **For external browser authentication**:
   ```
   cp .env.browser.example .env
   ```
   Then edit the `.env` file to set your Snowflake account details.

## Usage

### Running with uv

After installing the package, you can run the server directly with:

```
uv run snowflake-mcp

# Or you can be explicit about using stdio transport
uv run snowflake-mcp-stdio
```

This will start the stdio-based MCP server, which can be connected to Claude Desktop or any MCP client that supports stdio communication.

When using external browser authentication, a browser window will automatically open prompting you to log in to your Snowflake account.

### Claude Desktop Integration

1. In Claude Desktop, go to Settings → MCP Servers
2. Add a new server with the full path to your uv executable:
   ```yaml
   "snowflake-mcp-server": {
      "command": "uv",
      "args": [
         "--directory",
         "/<path-to-code>/snowflake-mcp-server",
         "run",
         "snowflake-mcp"
      ]
   }
   ```
   
   Or explicitly specify the stdio transport:
   
   ```yaml
   "snowflake-mcp-server": {
      "command": "uv",
      "args": [
         "--directory",
         "/<path-to-code>/snowflake-mcp-server",
         "run",
         "snowflake-mcp-stdio"
      ]
   }
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
- "Compare data from views in different databases by querying SALES.CUSTOMER_METRICS and MARKETING.CAMPAIGN_RESULTS"

## File Output for Large Datasets

The server includes intelligent output management for handling large query results that exceed AI model token limits.

### Configuration

Add these settings to your `.env` file to configure file output behavior:

```bash
# CRITICAL: Client working directory (set by MCP client)
# This prevents files from being written to the MCP server directory
MCP_CLIENT_ROOT=/path/to/your/project

# Model Configuration (source: https://llm-stats.com)
MODEL_NAME=claude-4-sonnet
MODEL_CONTEXT_TOKEN_LIMIT=200000
MODEL_SAFETY_MARGIN=0.7

# Output Behavior
DEFAULT_OUTPUT=auto        # auto|screen|file
DEFAULT_FILE_FORMAT=csv    # csv|json
DEFAULT_OUTPUT_DIR=./query_results  # Relative to MCP_CLIENT_ROOT
AUTO_GENERATE_FILENAME=true
FILENAME_PATTERN=query_{date}_{time}
```

**🚨 Important**: The `MCP_CLIENT_ROOT` environment variable **must** be set to your project's root directory. This ensures files are written to your project, not the MCP server's installation directory.

### Usage Examples

**Simple queries (recommended approach):**
```json
{
  "name": "execute_query",
  "arguments": {
    "query": "SELECT * FROM large_customer_data"
  }
}
```
*Server automatically decides screen vs file output based on result size*

**Force file output for large datasets:**
```json
{
  "name": "execute_query", 
  "arguments": {
    "query": "SELECT * FROM transaction_history WHERE year = 2024",
    "output": "file",
    "format": "csv"
  }
}
```
*Uses environment defaults for location and filename*

**Custom file export:**
```json
{
  "name": "execute_query",
  "arguments": {
    "query": "SELECT customer_id, total_revenue FROM sales_summary",
    "output": "file",
    "format": "json", 
    "location": "./reports",
    "filename": "q4_revenue_analysis"
  }
}
```
*Saves as ./reports/q4_revenue_analysis.json*

**Force inline display:**
```json
{
  "name": "execute_query",
  "arguments": {
    "query": "SELECT COUNT(*) FROM users",
    "output": "screen"
  }
}
```
*Always shows results inline regardless of size*

When file output is used, you'll receive detailed information about the generated file including path, size, and row count, allowing you to read and analyze the results separately.

### Working Directory Handling

**🔧 How It Works:**

- **`MCP_CLIENT_ROOT`**: The calling project's root directory (e.g., `/Users/yourname/projects/highway-snowflake`)
- **`DEFAULT_OUTPUT_DIR`**: Relative path within the client project (e.g., `./query_results`)
- **Final Path**: Files written to `{MCP_CLIENT_ROOT}/{DEFAULT_OUTPUT_DIR}/filename.csv`

**🛡️ Security Features:**
- **Never writes to MCP server directory** - prevents data pollution
- **Requires `MCP_CLIENT_ROOT`** - fails safely if not configured
- **Path validation** - blocks attempts to write outside client project

**⚙️ Claude Desktop Setup:**
```yaml
"snowflake-mcp-server": {
  "command": "uv",
  "args": ["--directory", "/path/to/snowflake-mcp-server", "run", "snowflake-mcp"],
  "env": {
    "MCP_CLIENT_ROOT": "/Users/yourname/projects/your-current-project",
    "SNOWFLAKE_ACCOUNT": "your-account",
    "SNOWFLAKE_USER": "your-user"
  }
}
```

### Configuration

Connection pooling behavior can be configured through environment variables:

- `SNOWFLAKE_CONN_REFRESH_HOURS`: Time interval in hours between connection refreshes (default: 8)

Example `.env` configuration:
```
# Set connection to refresh every 4 hours
SNOWFLAKE_CONN_REFRESH_HOURS=4
```

## Authentication Methods

### Private Key Authentication

This method uses a service account and private key for non-interactive authentication, ideal for automated processes.

1. Create a key pair for your Snowflake user following [Snowflake documentation](https://docs.snowflake.com/en/user-guide/key-pair-auth)
2. Set `SNOWFLAKE_AUTH_TYPE=private_key` in your `.env` file
3. Provide the path to your private key in `SNOWFLAKE_PRIVATE_KEY_PATH`

### External Browser Authentication

This method opens a browser window for interactive authentication.

1. Set `SNOWFLAKE_AUTH_TYPE=external_browser` in your `.env` file
2. When you start the server, a browser window will open asking you to log in
3. After authentication, the session will remain active for the duration specified by your Snowflake account settings

## Security Considerations

This server:
- Enforces configurable SQL command restrictions (default: SELECT, SHOW, DESCRIBE, EXPLAIN, WITH, and UNION statements)
- Automatically adds LIMIT clauses to prevent large result sets
- Uses secure authentication methods for connections to Snowflake
- Validates inputs to prevent SQL injection
- Allows customization of allowed SQL commands via ALLOWED_SQL_COMMANDS environment variable for operational flexibility

⚠️ **Important**: Keep your `.env` file secure and never commit it to version control. The `.gitignore` file is configured to exclude it.

## Development

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

### Running Tests

```
pytest
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Technical Details

This project uses:
- [Snowflake Connector Python](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector) for connecting to Snowflake
- [MCP (Model Context Protocol)](https://github.com/anthropics/anthropic-cookbook/tree/main/mcp) for interacting with Claude
- [Pydantic](https://docs.pydantic.dev/) for data validation
- [python-dotenv](https://github.com/theskumar/python-dotenv) for environment variable management
