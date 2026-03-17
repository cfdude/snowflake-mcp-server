# FastMCP 3.0 Migration & Architecture Overhaul

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Snowflake MCP server from the low-level `mcp` SDK to FastMCP 3.0, adding Streamable HTTP transport alongside STDIO, upgrading Pydantic to v2 with `pydantic-settings`, restructuring tests with `pytest`, and removing ~5,600 lines of orphaned infrastructure code.

**Architecture:** Two entry points share a single FastMCP server instance: `run_stdio.py` (Claude Desktop) and `run_http.py` (PM2/Claude Code on port 8000). Tools are registered via `@mcp.tool` decorators with type-driven schemas. Snowflake connections are managed via FastMCP's `@mcp.lifespan` for pool setup/teardown and `Depends()` for per-request connection injection. Configuration uses `pydantic-settings` `BaseSettings` to auto-load from env vars. Orphaned rate limiting, monitoring, and security modules are archived.

**Tech Stack:** Python 3.13, FastMCP 3.0.x, snowflake-connector-python, Pydantic v2, pydantic-settings, sqlglot, pytest + pytest-asyncio, PM2

---

## Pre-Implementation: Orphaned Code Audit

The following modules are **confirmed orphaned** — they are never imported by `main.py` (the only active entry point) or any module in the active import chain:

| Module | Lines | Status | Evidence |
|--------|-------|--------|----------|
| `rate_limiting/rate_limiter.py` | 682 | Orphaned | Zero imports from main.py or active modules |
| `rate_limiting/circuit_breaker.py` | 630 | Orphaned | Zero imports |
| `rate_limiting/quota_manager.py` | 865 | Orphaned | Zero imports |
| `rate_limiting/backoff.py` | 612 | Orphaned | Zero imports |
| `rate_limiting/__init__.py` | 44 | Orphaned | Zero imports |
| `monitoring/alerts.py` | 701 | Orphaned | Only imported by other orphaned monitoring code |
| `monitoring/dashboards.py` | 605 | Orphaned | Zero imports from active code |
| `monitoring/metrics.py` | 557 | Orphaned | Zero imports from active code |
| `monitoring/query_tracker.py` | 688 | Orphaned | Zero imports from active code |
| `monitoring/structured_logging.py` | 440 | Orphaned | Only imported by monitoring/__init__.py and orphaned alert/tracker code |
| `monitoring/__init__.py` | 27 | Orphaned | Zero imports from active code |
| `security/authentication.py` | 740 | Orphaned | Zero imports; API key auth disabled by default |
| `security/sql_injection.py` | 761 | Orphaned | Zero imports; main.py uses sqlglot directly |
| `security/__init__.py` | 18 | Orphaned | Zero imports |
| `utils/connection_multiplexer.py` | 425 | Orphaned | Zero imports from active code |
| `utils/client_isolation.py` | 439 | Orphaned | Zero imports from active code |
| `utils/resource_allocator.py` | 524 | Orphaned | Zero imports from active code |
| `utils/session_manager.py` | 369 | Orphaned | Only imported by orphaned monitoring/metrics.py |
| `utils/request_batching.py` | 238 | Orphaned | Zero imports from active code |
| `utils/stdio_pacing.py` | 135 | Orphaned | Zero imports from active code |
| `utils/template.py` | 175 | Orphaned | Zero imports from active code |
| `utils/connection_metrics.py` | 242 | Orphaned | Zero imports from active code |
| `utils/log_manager.py` | 370 | Orphaned | Zero imports from active code |
| **TOTAL ORPHANED** | **9,287** | | **64% of the 14,589-line codebase** |

### Modules that ARE actively used (keep):

| Module | Lines | Used By |
|--------|-------|---------|
| `main.py` | 980 | Entry point (will be rewritten) |
| `config.py` | 414 | main.py → `get_config()` |
| `transports/http_server.py` | 484 | main.py → `run_http_server()` (will be replaced) |
| `utils/snowflake_conn.py` | 373 | main.py → connection management |
| `utils/async_pool.py` | 361 | main.py → `ConnectionPoolConfig`, pool init |
| `utils/async_database.py` | 400 | main.py → `get_isolated_database_ops`, `get_transactional_database_ops` |
| `utils/output_handler.py` | 321 | main.py → `ResultOutputHandler` |
| `utils/token_estimator.py` | 222 | output_handler.py → `TokenEstimator` |
| `utils/request_context.py` | 215 | main.py → `RequestContext`, `request_context` |
| `utils/contextual_logging.py` | 125 | main.py → logging setup |
| `utils/cursor_management.py` | 153 | async_database.py → `get_managed_cursor` |
| `utils/health_monitor.py` | 150 | async_pool.py → `health_monitor` |
| `utils/transaction_manager.py` | 100 | async_database.py (transactional ops) |

---

## File Map

### Files to Delete (Stage 1: Archive orphaned code)
- `snowflake_mcp_server/rate_limiting/` — entire directory (2,833 lines)
- `snowflake_mcp_server/monitoring/` — entire directory (3,018 lines)
- `snowflake_mcp_server/security/` — entire directory (1,519 lines)
- `snowflake_mcp_server/utils/connection_multiplexer.py` (425 lines)
- `snowflake_mcp_server/utils/client_isolation.py` (439 lines)
- `snowflake_mcp_server/utils/resource_allocator.py` (524 lines)
- `snowflake_mcp_server/utils/session_manager.py` (369 lines)
- `snowflake_mcp_server/utils/request_batching.py` (238 lines)
- `snowflake_mcp_server/utils/stdio_pacing.py` (135 lines)
- `snowflake_mcp_server/utils/template.py` (175 lines)
- `snowflake_mcp_server/utils/connection_metrics.py` (242 lines)
- `snowflake_mcp_server/utils/log_manager.py` (370 lines)
- `snowflake_mcp_server/transports/http_server.py` (484 lines, replaced by FastMCP)
- `snowflake_mcp_server/transports/__init__.py` (0 lines)
- `snowflake_mcp_server/main.py` (980 lines, replaced by server.py + entry points)
- `test_mcp_server.py` (root, 429 lines, replaced by proper pytest)
- `test_mcp_functionality.py` (root, 414 lines, replaced by proper pytest)

### Files to Create
- `snowflake_mcp_server/server.py` — FastMCP instance, tool definitions, lifespan
- `snowflake_mcp_server/dependencies.py` — Depends() providers for Snowflake connections
- `snowflake_mcp_server/run_stdio.py` — STDIO entry point
- `snowflake_mcp_server/run_http.py` — HTTP entry point
- `tests/conftest.py` — Shared pytest fixtures
- `tests/test_tools.py` — Tool unit tests using FastMCP test client
- `tests/test_config.py` — Config/settings tests
- `tests/test_dependencies.py` — Dependency injection tests

### Files to Modify
- `snowflake_mcp_server/config.py` — Migrate to pydantic-settings BaseSettings, remove orphaned config sections
- `snowflake_mcp_server/utils/snowflake_conn.py` — Remove LegacyConnectionManager, simplify
- `snowflake_mcp_server/utils/async_database.py` — Remove orphaned contextual_logging imports
- `snowflake_mcp_server/utils/async_pool.py` — Remove orphaned health_monitor imports, simplify
- `snowflake_mcp_server/utils/request_context.py` — Simplify (FastMCP Context replaces some of this)
- `snowflake_mcp_server/utils/contextual_logging.py` — Simplify or replace with FastMCP logging middleware
- `pyproject.toml` — Update dependencies, entry points
- `ecosystem.config.js` — Update for new entry points
- `.env.example` — Update for simplified config

### Files to Keep As-Is
- `snowflake_mcp_server/utils/output_handler.py` — Custom business logic, no changes needed
- `snowflake_mcp_server/utils/token_estimator.py` — Custom business logic, no changes needed
- `snowflake_mcp_server/utils/cursor_management.py` — Used by async_database.py
- `snowflake_mcp_server/utils/transaction_manager.py` — Used by async_database.py
- `snowflake_mcp_server/utils/health_monitor.py` — Used by async_pool.py (evaluate later)

---

## Stage 1: Archive Orphaned Code

### Task 1: Create archive branch and remove orphaned modules

We archive rather than delete so the git history preserves the code and it's recoverable.

**Files:**
- Delete: All files listed in "Files to Delete" above (orphaned code only, NOT main.py/transports yet)

- [ ] **Step 1: Create a git tag preserving the pre-migration state**

```bash
git tag pre-fastmcp-migration -m "Snapshot before FastMCP 3.0 migration and orphaned code removal"
```

- [ ] **Step 2: Remove orphaned rate_limiting directory**

```bash
rm -rf snowflake_mcp_server/rate_limiting/
```

- [ ] **Step 3: Remove orphaned monitoring directory**

```bash
rm -rf snowflake_mcp_server/monitoring/
```

- [ ] **Step 4: Remove orphaned security directory**

```bash
rm -rf snowflake_mcp_server/security/
```

- [ ] **Step 5: Remove orphaned utility modules**

```bash
rm snowflake_mcp_server/utils/connection_multiplexer.py
rm snowflake_mcp_server/utils/client_isolation.py
rm snowflake_mcp_server/utils/resource_allocator.py
rm snowflake_mcp_server/utils/session_manager.py
rm snowflake_mcp_server/utils/request_batching.py
rm snowflake_mcp_server/utils/stdio_pacing.py
rm snowflake_mcp_server/utils/template.py
rm snowflake_mcp_server/utils/connection_metrics.py
rm snowflake_mcp_server/utils/log_manager.py
```

- [ ] **Step 6: Remove orphaned root-level test files**

```bash
rm test_mcp_server.py
rm test_mcp_functionality.py
```

- [ ] **Step 7: Verify no active code is broken**

Run: `cd /Users/robsherman/Servers/snowflake-mcp-server && uv run python -c "from snowflake_mcp_server.main import create_server; print('OK')"`

Expected: `OK` — main.py should still import cleanly because it never imported the deleted modules.

If there are import errors, they indicate hidden dependencies we missed — fix them before proceeding.

- [ ] **Step 8: Verify with grep that no active code references deleted modules**

```bash
rg -l "rate_limiting|monitoring\.(alerts|dashboards|metrics|query_tracker|structured_logging)|security\.(authentication|sql_injection)|connection_multiplexer|client_isolation|resource_allocator|session_manager|request_batching|stdio_pacing|template|connection_metrics|log_manager" snowflake_mcp_server/ --glob '*.py' --glob '!__pycache__'
```

Expected: Zero results, or only `__init__.py` files that also need cleanup.

- [ ] **Step 9: Clean up any __init__.py files that reference deleted modules**

Check and clean:
- `snowflake_mcp_server/utils/__init__.py` — should be empty or minimal
- `snowflake_mcp_server/__init__.py` — should only have version

- [ ] **Step 10: Commit archive removal**

```bash
git add -A
git commit -m "refactor: remove 9,287 lines of orphaned infrastructure code

Remove rate_limiting/ (2,833 lines), monitoring/ (3,018 lines),
security/ (1,519 lines), and 12 orphaned utility modules (1,917 lines).

None of these modules were imported by main.py or any active code path.
Rate limiting, monitoring dashboards/alerts, API key authentication,
and various connection management utilities were never wired into the
running server.

Pre-migration state preserved in tag: pre-fastmcp-migration"
```

---

## Stage 2: Dependency & Config Upgrade

### Task 2: Update dependencies in pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update pyproject.toml with new dependencies**

Replace the current `[project]` dependencies and scripts sections:

```toml
[project]
name = "snowflake-mcp-server"
version = "1.0.0"
description = "MCP server for performing read-only operations against Snowflake"
readme = "README.md"
requires-python = ">=3.13,<3.14"
license = { text = "MIT" }
dependencies = [
    "fastmcp>=3.0.0",
    "snowflake-connector-python>=3.8.0",
    "pydantic>=2.4.2",
    "pydantic-settings>=2.0.0",
    "python-dotenv>=1.0.0",
    "cryptography>=41.0.0",
    "anyio>=3.7.1",
    "sqlglot>=11.5.5",
    "aiofiles>=23.2.0",
]

[project.scripts]
snowflake-mcp = "snowflake_mcp_server.run_stdio:main"
snowflake-mcp-stdio = "snowflake_mcp_server.run_stdio:main"
snowflake-mcp-http = "snowflake_mcp_server.run_http:main"

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.0.0",
    "ruff>=0.1.0",
    "mypy>=1.6.0",
]
```

Note what's **removed**: `asyncio-pool`, `asyncpg`, `fastapi`, `uvicorn`, `websockets`, `python-multipart`, `httpx`, `prometheus-client`, `structlog`, `tenacity`, `slowapi`. FastMCP bundles its own HTTP server (uvicorn + starlette) and we no longer need the rest.

- [ ] **Step 2: Lock dependencies**

Run: `cd /Users/robsherman/Servers/snowflake-mcp-server && uv lock`

Expected: Clean lock resolution. If there are conflicts between FastMCP and snowflake-connector-python, resolve by checking compatible version ranges.

- [ ] **Step 3: Install dependencies**

Run: `uv sync`

Expected: Clean install.

- [ ] **Step 4: Verify FastMCP is installed and accessible**

Run: `uv run python -c "import fastmcp; print(f'FastMCP {fastmcp.__version__}')"`

Expected: `FastMCP 3.x.x`

- [ ] **Step 5: Commit dependency changes**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: update dependencies for FastMCP 3.0 migration

Replace mcp SDK with fastmcp>=3.0.0.
Add pydantic-settings for config management.
Remove orphaned dependencies: asyncio-pool, asyncpg, fastapi, uvicorn,
websockets, python-multipart, httpx, prometheus-client, structlog,
tenacity, slowapi. FastMCP bundles its own HTTP server."
```

### Task 3: Migrate config.py to pydantic-settings v2

**Files:**
- Modify: `snowflake_mcp_server/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test for config loading from env vars**

Create `tests/test_config.py`:

```python
"""Tests for configuration loading via pydantic-settings."""

import os
from unittest.mock import patch

import pytest


def _minimal_env():
    """Return minimal env vars needed for config to load."""
    return {
        "SNOWFLAKE_ACCOUNT": "testaccount",
        "SNOWFLAKE_USER": "testuser",
        "SNOWFLAKE_AUTH_TYPE": "private_key",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
    }


class TestServerConfig:
    """Test ServerConfig loads from environment variables."""

    def test_loads_minimal_config(self):
        """Config loads with only required env vars set."""
        from snowflake_mcp_server.config import ServerConfig

        with patch.dict(os.environ, _minimal_env(), clear=False):
            config = ServerConfig()
            assert config.snowflake.account == "testaccount"
            assert config.snowflake.user == "testuser"
            assert config.http.port == 8000

    def test_default_values(self):
        """Config applies sensible defaults."""
        from snowflake_mcp_server.config import ServerConfig

        with patch.dict(os.environ, _minimal_env(), clear=False):
            config = ServerConfig()
            assert config.security.readonly_mode is True
            assert config.performance.default_query_limit == 100
            assert config.output.default_output == "auto"

    def test_custom_port_from_env(self):
        """MCP_HTTP_PORT env var overrides default port."""
        from snowflake_mcp_server.config import ServerConfig

        env = {**_minimal_env(), "MCP_HTTP_PORT": "9000"}
        with patch.dict(os.environ, env, clear=False):
            config = ServerConfig()
            assert config.http.port == 9000

    def test_allowed_sql_commands_from_csv_string(self):
        """ALLOWED_SQL_COMMANDS parses comma-separated string."""
        from snowflake_mcp_server.config import ServerConfig

        env = {**_minimal_env(), "ALLOWED_SQL_COMMANDS": "select,show,with"}
        with patch.dict(os.environ, env, clear=False):
            config = ServerConfig()
            assert config.security.allowed_sql_commands == ["select", "show", "with"]

    def test_missing_required_account_raises(self):
        """Config raises if SNOWFLAKE_ACCOUNT is missing."""
        from snowflake_mcp_server.config import ServerConfig

        env = {"SNOWFLAKE_USER": "testuser"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(Exception):
                ServerConfig()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL — `ServerConfig` is a `BaseModel`, not `BaseSettings`, so it doesn't auto-read env vars.

- [ ] **Step 3: Rewrite config.py using pydantic-settings**

Replace the entire contents of `snowflake_mcp_server/config.py`:

```python
"""Environment-based configuration management for Snowflake MCP Server.

Uses pydantic-settings BaseSettings to auto-load from environment variables.
Each nested config class has an env_prefix that maps to env var names.
"""

import logging
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SnowflakeConnectionConfig(BaseSettings):
    """Snowflake connection configuration."""

    model_config = SettingsConfigDict(env_prefix="SNOWFLAKE_")

    account: str
    user: str
    auth_type: str = "private_key"
    private_key_path: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    private_key: Optional[str] = None
    warehouse: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = None
    role: Optional[str] = None

    @field_validator("auth_type", mode="before")
    @classmethod
    def normalize_auth_type(cls, v: str) -> str:
        return v.lower()


class ConnectionPoolConfig(BaseSettings):
    """Connection pool configuration."""

    model_config = SettingsConfigDict(env_prefix="SNOWFLAKE_POOL_")

    min_size: int = 2
    max_size: int = 10
    connection_timeout: float = 30.0
    health_check_interval: int = 5
    max_inactive_time: int = 30
    refresh_hours: int = 8

    @field_validator("max_size", mode="before")
    @classmethod
    def validate_max_size(cls, v: int) -> int:
        if int(v) < 1:
            raise ValueError("max_size must be at least 1")
        return int(v)


class HttpServerConfig(BaseSettings):
    """HTTP server configuration."""

    model_config = SettingsConfigDict(env_prefix="MCP_HTTP_")

    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout: int = 300

    @field_validator("port", mode="before")
    @classmethod
    def validate_port(cls, v: int) -> int:
        v = int(v)
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v


class PerformanceConfig(BaseSettings):
    """Performance and resource configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    max_concurrent_requests: int = 10
    default_query_limit: int = 100
    max_query_limit: int = 10000


class SecurityConfig(BaseSettings):
    """Security configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    allowed_sql_commands: List[str] = [
        "select", "show", "describe", "explain", "with", "union", "use",
    ]
    readonly_mode: bool = True

    @field_validator("allowed_sql_commands", mode="before")
    @classmethod
    def parse_allowed_sql_commands(cls, v):
        if isinstance(v, str):
            commands = [cmd.strip().lower() for cmd in v.split(",") if cmd.strip()]
            return commands if commands else ["select", "show", "describe", "explain", "with", "union"]
        elif isinstance(v, list):
            return [cmd.lower().strip() for cmd in v if cmd and cmd.strip()]
        return v


class OutputConfig(BaseSettings):
    """Output and token management configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    model_name: str = "unknown"
    model_token_limit: int = 100000
    safety_margin: float = 0.7
    default_output: str = "auto"
    default_file_format: str = "csv"
    default_output_dir: str = "./query_results"
    client_root: Optional[str] = None
    screen_output_row_threshold: int = 1000
    auto_generate_filename: bool = True
    filename_pattern: str = "query_{date}_{time}"
    token_sample_size: int = 100
    log_token_estimation: bool = False

    @field_validator("default_output", mode="before")
    @classmethod
    def validate_output_mode(cls, v: str) -> str:
        if v not in ("auto", "screen", "file"):
            raise ValueError("default_output must be 'auto', 'screen', or 'file'")
        return v

    @field_validator("default_file_format", mode="before")
    @classmethod
    def validate_file_format(cls, v: str) -> str:
        if v not in ("csv", "json"):
            raise ValueError("default_file_format must be 'csv' or 'json'")
        return v


class ServerConfig(BaseSettings):
    """Complete server configuration.

    Nested models are instantiated automatically from env vars.
    Example: SNOWFLAKE_ACCOUNT, MCP_HTTP_PORT, ALLOWED_SQL_COMMANDS, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
    )

    environment: str = "production"
    app_version: str = "1.0.0"

    snowflake: SnowflakeConnectionConfig = SnowflakeConnectionConfig()
    pool: ConnectionPoolConfig = ConnectionPoolConfig()
    http: HttpServerConfig = HttpServerConfig()
    performance: PerformanceConfig = PerformanceConfig()
    security: SecurityConfig = SecurityConfig()
    output: OutputConfig = OutputConfig()


# Global configuration instance
_config: Optional[ServerConfig] = None


def get_config() -> ServerConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = ServerConfig()
    return _config


def reload_config() -> ServerConfig:
    """Reload configuration from environment variables."""
    global _config
    _config = ServerConfig()
    return _config
```

Key changes:
- `BaseModel` → `BaseSettings` (auto-reads env vars)
- `@validator` (v1) → `@field_validator` (v2)
- Removed the 100-line `load_config()` function — `BaseSettings()` does it automatically
- Removed `LoggingConfig`, `MonitoringConfig`, `DevelopmentConfig` — orphaned, not used by active code
- Removed CORS config — FastMCP handles CORS internally
- Each nested config has its own `env_prefix` so `SNOWFLAKE_ACCOUNT` maps to `snowflake.account`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit config migration**

```bash
git add snowflake_mcp_server/config.py tests/test_config.py
git commit -m "refactor: migrate config to pydantic-settings v2

Replace BaseModel with BaseSettings for automatic env var loading.
Migrate all validators from Pydantic v1 @validator to v2 @field_validator.
Remove 100-line load_config() function — BaseSettings handles it.
Remove orphaned config sections: LoggingConfig, MonitoringConfig,
DevelopmentConfig, CORS config.
Add tests for config loading from env vars."
```

---

## Stage 3: FastMCP Server Implementation

### Task 4: Create the dependency injection module

**Files:**
- Create: `snowflake_mcp_server/dependencies.py`
- Create: `tests/test_dependencies.py`

- [ ] **Step 1: Write failing test for dependency provider**

Create `tests/test_dependencies.py`:

```python
"""Tests for dependency injection providers."""

import pytest


class TestGetSnowflakeConfig:
    """Test Snowflake config dependency."""

    def test_returns_config_from_env(self):
        """get_snowflake_config returns a SnowflakeConfig from env."""
        import os
        from unittest.mock import patch

        env = {
            "SNOWFLAKE_ACCOUNT": "testaccount",
            "SNOWFLAKE_USER": "testuser",
            "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
        }
        with patch.dict(os.environ, env, clear=False):
            from snowflake_mcp_server.dependencies import get_snowflake_config

            config = get_snowflake_config()
            assert config.account == "testaccount"
            assert config.user == "testuser"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dependencies.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create dependencies.py**

Create `snowflake_mcp_server/dependencies.py`:

```python
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
    IsolatedDatabaseOps,
    get_isolated_database_ops,
    get_transactional_database_ops,
)
from snowflake_mcp_server.utils.request_context import RequestContext, request_context
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dependencies.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add snowflake_mcp_server/dependencies.py tests/test_dependencies.py
git commit -m "feat: add dependency injection providers for FastMCP tools

Depends()-compatible providers for Snowflake config, server config,
and isolated database operations. Hidden from MCP tool schemas."
```

### Task 5: Create the FastMCP server with tool definitions

**Files:**
- Create: `snowflake_mcp_server/server.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing test for tool registration**

Create `tests/test_tools.py`:

```python
"""Tests for MCP tool definitions using FastMCP test client."""

import pytest


class TestToolRegistration:
    """Test that all tools are properly registered."""

    @pytest.mark.asyncio
    async def test_server_has_all_tools(self):
        """Server exposes all 5 Snowflake tools."""
        from snowflake_mcp_server.server import mcp

        async with mcp.test_client() as client:
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

        async with mcp.test_client() as client:
            tools = await client.list_tools()
            db_tool = next(t for t in tools if t.name == "list_databases")
            # No required params
            required = db_tool.inputSchema.get("required", [])
            assert required == []

    @pytest.mark.asyncio
    async def test_execute_query_tool_schema(self):
        """execute_query requires 'query' parameter."""
        from snowflake_mcp_server.server import mcp

        async with mcp.test_client() as client:
            tools = await client.list_tools()
            eq_tool = next(t for t in tools if t.name == "execute_query")
            assert "query" in eq_tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_tool_annotations_are_readonly(self):
        """All tools should have readOnlyHint=True annotation."""
        from snowflake_mcp_server.server import mcp

        async with mcp.test_client() as client:
            tools = await client.list_tools()
            for tool in tools:
                if tool.annotations:
                    assert tool.annotations.readOnlyHint is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`

Expected: FAIL — `snowflake_mcp_server.server` doesn't exist.

- [ ] **Step 3: Create server.py with FastMCP instance and tool definitions**

Create `snowflake_mcp_server/server.py`:

```python
"""FastMCP server for Snowflake read-only operations.

This module defines the FastMCP server instance and all tool definitions.
Tools use @mcp.tool decorators with type hints for automatic schema generation.
Snowflake connections are injected via FastMCP's Depends() pattern.
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
    stateless_http=True,
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
    schema: Annotated[Optional[str], Field(description="The schema name (optional, uses current schema if not provided)")] = None,
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
    schema: Annotated[Optional[str], Field(description="The schema name (optional)")] = None,
) -> str:
    """Get detailed information about a specific view."""
    try:
        async with request_context("describe_view", {"database": database, "view_name": view_name}, "fastmcp") as ctx:
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
    schema: Annotated[Optional[str], Field(description="The schema name (optional)")] = None,
    limit: Annotated[int, Field(description="Maximum number of rows to return")] = 10,
) -> str:
    """Query data from a specific view with optional limit."""
    try:
        async with request_context("query_view", {"database": database, "view_name": view_name}, "fastmcp") as ctx:
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
    use_transaction: Annotated[bool, Field(description="Enable transaction boundary management")] = False,
    auto_commit: Annotated[bool, Field(description="Auto-commit transaction when use_transaction is true")] = True,
    output: Annotated[Optional[str], Field(description="Output mode: 'auto' (recommended), 'screen', or 'file'")] = None,
    format: Annotated[Optional[str], Field(description="File format: 'csv' or 'json'. Only used when output is 'file'")] = None,
    location: Annotated[Optional[str], Field(description="Output directory path. Only used when output is 'file'")] = None,
    filename: Annotated[Optional[str], Field(description="Custom output filename")] = None,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`

Expected: All 4 tests PASS (tool registration tests don't need Snowflake — they just check the schema).

- [ ] **Step 5: Commit**

```bash
git add snowflake_mcp_server/server.py tests/test_tools.py
git commit -m "feat: implement FastMCP server with all tool definitions

5 tools registered via @mcp.tool decorators with type-driven schemas:
list_databases, list_views, describe_view, query_view, execute_query.

All tools annotated with readOnlyHint=True.
execute_query has 300s timeout and smart output handling.
Health endpoint via @mcp.custom_route('/health').
Stateless HTTP mode enabled for memory efficiency."
```

### Task 6: Create STDIO and HTTP entry points

**Files:**
- Create: `snowflake_mcp_server/run_stdio.py`
- Create: `snowflake_mcp_server/run_http.py`

- [ ] **Step 1: Create STDIO entry point**

Create `snowflake_mcp_server/run_stdio.py`:

```python
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
```

- [ ] **Step 2: Create HTTP entry point**

Create `snowflake_mcp_server/run_http.py`:

```python
"""HTTP entry point for PM2 / Claude Code.

Run via: uv run snowflake-mcp-http
Or: python -m snowflake_mcp_server.run_http
Or: pm2 start ecosystem.config.js

Serves Streamable HTTP transport on port 8000 (configurable via MCP_HTTP_PORT).
"""

import logging
import os
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

    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify STDIO entry point loads without error**

Run: `uv run python -c "from snowflake_mcp_server.run_stdio import main; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Verify HTTP entry point loads without error**

Run: `uv run python -c "from snowflake_mcp_server.run_http import main; print('OK')"`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add snowflake_mcp_server/run_stdio.py snowflake_mcp_server/run_http.py
git commit -m "feat: add STDIO and HTTP entry points

run_stdio.py: For Claude Desktop (mcp.run(transport='stdio'))
run_http.py: For PM2/Claude Code (mcp.run(transport='http', port=8000))
Both read config from env vars via pydantic-settings."
```

---

## Stage 4: Clean Up Old Code & Update Infrastructure

### Task 7: Remove old main.py and transports

**Files:**
- Delete: `snowflake_mcp_server/main.py`
- Delete: `snowflake_mcp_server/transports/http_server.py`
- Delete: `snowflake_mcp_server/transports/__init__.py`

- [ ] **Step 1: Remove old entry points**

```bash
rm snowflake_mcp_server/main.py
rm -rf snowflake_mcp_server/transports/
```

- [ ] **Step 2: Remove contextual_logging.py (replaced by FastMCP logging)**

```bash
rm snowflake_mcp_server/utils/contextual_logging.py
```

- [ ] **Step 3: Clean up async_database.py — remove contextual_logging imports**

In `snowflake_mcp_server/utils/async_database.py`, find and remove all lines that import from `contextual_logging`:

```python
# Remove these lines wherever they appear:
from .contextual_logging import log_database_operation
from .contextual_logging import log_transaction_event
from .contextual_logging import log_connection_event
```

And remove any calls to `log_database_operation()`, `log_transaction_event()`, `log_connection_event()` — replace with standard `logger.info()` / `logger.debug()` calls.

- [ ] **Step 4: Clean up async_pool.py — remove health_monitor imports**

In `snowflake_mcp_server/utils/async_pool.py`, find and remove the lazy imports:

```python
# Remove these lines wherever they appear:
from .health_monitor import health_monitor
```

Replace any `health_monitor.record_*()` calls with standard `logger.info()` / `logger.debug()`.

- [ ] **Step 5: Remove snowflake_conn.py LegacyConnectionManager**

In `snowflake_mcp_server/utils/snowflake_conn.py`, delete the `LegacyConnectionManager` class (lines 338-373) and the `legacy_connection_manager` singleton at the bottom.

- [ ] **Step 6: Verify the server still imports cleanly**

Run: `uv run python -c "from snowflake_mcp_server.server import mcp; print(f'Tools: {len(mcp._tool_manager._tools)}')"`

Expected: `Tools: 5`

- [ ] **Step 7: Run all tests**

Run: `uv run pytest tests/ -v`

Expected: All tests pass.

- [ ] **Step 8: Commit cleanup**

```bash
git add -A
git commit -m "refactor: remove old main.py, transports, and dead imports

Delete main.py (980 lines), transports/ (484 lines), contextual_logging.
Clean up async_database.py and async_pool.py to remove imports from
deleted modules. Remove LegacyConnectionManager from snowflake_conn.py."
```

### Task 8: Update ecosystem.config.js for new entry points

**Files:**
- Modify: `ecosystem.config.js`

- [ ] **Step 1: Update ecosystem.config.js**

Replace the full contents:

```javascript
module.exports = {
  apps: [
    {
      // Snowflake MCP Server - HTTP mode (PM2 managed, shared by Claude Code)
      name: 'snowflake-mcp-http',
      script: 'uv',
      args: 'run snowflake-mcp-http',
      cwd: '/Users/robsherman/Servers/snowflake-mcp-server',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      env: {
        NODE_ENV: 'production',
        PYTHONPATH: '/Users/robsherman/Servers/snowflake-mcp-server',
        SNOWFLAKE_CONN_REFRESH_HOURS: '8',
      },
      // Logging
      log_file: './logs/snowflake-mcp-http.log',
      error_file: './logs/snowflake-mcp-http-error.log',
      out_file: './logs/snowflake-mcp-http-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,

      // Auto-restart
      autorestart: true,
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',

      // Memory limit
      max_memory_restart: '300M',

      // Health monitoring
      health_check_url: 'http://localhost:8000/health',
      health_check_grace_period: 10000,

      // Process management
      kill_timeout: 5000,
      listen_timeout: 8000,
      exp_backoff_restart_delay: 100,
    },
  ],
};
```

Note: Removed the STDIO PM2 entry — STDIO is launched on-demand by Claude Desktop, not managed by PM2.

- [ ] **Step 2: Commit**

```bash
git add ecosystem.config.js
git commit -m "chore: update ecosystem.config.js for FastMCP entry points

Update script args for new run_http entry point.
Remove STDIO PM2 entry (Claude Desktop manages its own process).
Reduce max_memory_restart to 300M (down from 500M)."
```

### Task 9: Fix existing tests and restructure

**Files:**
- Modify: `tests/test_snowflake_conn.py` (fix broken import)
- Delete: `tests/test_async_integration.py` (references deleted modules)
- Delete: `tests/test_load_testing.py` (references deleted modules)
- Delete: `tests/test_multi_client.py` (references deleted modules)
- Delete: `tests/test_chaos_engineering.py` (references deleted modules)
- Create: `tests/conftest.py`

- [ ] **Step 1: Check which test files import from deleted modules**

```bash
rg "from.*rate_limiting|from.*monitoring|from.*security|from.*client_isolation|from.*connection_multiplexer|from.*session_manager|from.*resource_allocator|from.*main import|mcp_server_snowflake" tests/ --glob '*.py'
```

Fix or delete files that reference deleted modules.

- [ ] **Step 2: Fix test_snowflake_conn.py import path**

In `tests/test_snowflake_conn.py`, change:

```python
# FROM:
from mcp_server_snowflake.utils.snowflake_conn import (
# TO:
from snowflake_mcp_server.utils.snowflake_conn import (
```

- [ ] **Step 3: Remove test files that test deleted modules**

```bash
rm tests/test_async_integration.py
rm tests/test_load_testing.py
rm tests/test_multi_client.py
rm tests/test_chaos_engineering.py
rm tests/test_request_isolation.py
```

These all test infrastructure code that no longer exists.

- [ ] **Step 4: Create conftest.py with shared fixtures**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures for Snowflake MCP server tests."""

import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(autouse=True)
def mock_snowflake_env():
    """Provide minimal Snowflake env vars for all tests."""
    env = {
        "SNOWFLAKE_ACCOUNT": "testaccount",
        "SNOWFLAKE_USER": "testuser",
        "SNOWFLAKE_AUTH_TYPE": "private_key",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/path/to/key.p8",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def mock_private_key():
    """Mock RSA private key."""
    return MagicMock(spec=rsa.RSAPrivateKey)


@pytest.fixture
def mock_snowflake_connection():
    """Mock Snowflake connection object."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.description = []
    conn.cursor.return_value = cursor
    return conn
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v --tb=short`

Expected: All remaining tests pass.

- [ ] **Step 6: Commit test restructuring**

```bash
git add -A
git commit -m "test: restructure tests for FastMCP architecture

Fix broken import path in test_snowflake_conn.py.
Remove 5 test files that tested deleted infrastructure modules.
Add conftest.py with shared fixtures (mock env, mock connection).
Add test_config.py, test_dependencies.py, test_tools.py."
```

---

## Stage 5: Integration Verification & PM2 Deployment

### Task 10: Smoke test HTTP server

- [ ] **Step 1: Start the HTTP server locally**

```bash
cd /Users/robsherman/Servers/snowflake-mcp-server
uv run snowflake-mcp-http &
sleep 3
```

- [ ] **Step 2: Verify health endpoint**

```bash
curl -s http://localhost:8000/health | jq .
```

Expected: `{"status": "healthy", "version": "1.0.0", ...}`

- [ ] **Step 3: Verify MCP endpoint exists**

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","id":"1","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

Expected: JSON response with server capabilities (not a 404).

- [ ] **Step 4: Stop the test server**

```bash
kill %1
```

- [ ] **Step 5: Deploy via PM2**

```bash
pm2 stop snowflake-mcp-http 2>/dev/null; pm2 delete snowflake-mcp-http 2>/dev/null
cd /Users/robsherman/Servers/snowflake-mcp-server
pm2 start ecosystem.config.js
pm2 save
```

- [ ] **Step 6: Verify PM2 deployment**

```bash
pm2 list | grep snowflake
curl -s http://localhost:8000/health | jq .
```

Expected: PM2 shows `snowflake-mcp-http` as `online`, health endpoint responds.

### Task 11: Configure Claude Code

- [ ] **Step 1: Add Snowflake MCP to Claude Code user scope**

```bash
claude mcp add --scope user snowflake-mcp-server --transport http --url http://localhost:8000/mcp
```

- [ ] **Step 2: Remove old STDIO-based config if present**

Check `~/.claude/settings.json` and any project-level `.mcp.json` files for old STDIO entries pointing to this server. Remove them since the user-scoped HTTP config handles it globally.

Note: Claude Desktop should still use STDIO via its own config — that doesn't change.

- [ ] **Step 3: Verify in a new Claude Code session**

Start a new Claude Code session and confirm the Snowflake tools are available and working.

### Task 12: Final verification and commit

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 2: Verify line count reduction**

```bash
find snowflake_mcp_server -name '*.py' -not -path '*__pycache__*' -not -path '*.venv*' | xargs wc -l | tail -1
```

Expected: Significantly less than the original 14,589 lines.

- [ ] **Step 3: Verify no orphaned imports**

```bash
uv run python -c "
from snowflake_mcp_server.server import mcp
from snowflake_mcp_server.config import get_config
from snowflake_mcp_server.run_stdio import main as stdio_main
from snowflake_mcp_server.run_http import main as http_main
print('All imports clean')
"
```

Expected: `All imports clean`

---

## Summary of Commits

1. `refactor: remove 9,287 lines of orphaned infrastructure code` — Stage 1
2. `chore: update dependencies for FastMCP 3.0 migration` — Stage 2
3. `refactor: migrate config to pydantic-settings v2` — Stage 2
4. `feat: add dependency injection providers for FastMCP tools` — Stage 3
5. `feat: implement FastMCP server with all tool definitions` — Stage 3
6. `feat: add STDIO and HTTP entry points` — Stage 3
7. `refactor: remove old main.py, transports, and dead imports` — Stage 4
8. `chore: update ecosystem.config.js for FastMCP entry points` — Stage 4
9. `test: restructure tests for FastMCP architecture` — Stage 4

## Post-Implementation Verification Checklist

- [ ] `uv run pytest tests/ -v` — all tests pass
- [ ] `uv run python -c "from snowflake_mcp_server.server import mcp"` — imports clean
- [ ] `curl http://localhost:8000/health` — responds with status healthy
- [ ] `pm2 list` — `snowflake-mcp-http` online
- [ ] Claude Code session can call Snowflake tools via HTTP transport
- [ ] Claude Desktop can still use STDIO transport (unchanged)
- [ ] `~/SERVER_PORTS.md` — port 8000 entry already exists, no change needed
- [ ] No orphaned modules remain: `rg "rate_limiting|monitoring\.(alerts|dashboards)" snowflake_mcp_server/` returns zero results
