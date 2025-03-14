# CLAUDE.md - MCP Server Snowflake (Python)

## Build & Run Commands
- Setup: `uv pip install -e .` or `uv pip install -r requirements.txt`
- Start server: `python -m mcp_server_snowflake.main`
- Development mode: `uvicorn mcp_server_snowflake.main:app --reload`

## Test Commands
- Run all tests: `pytest`
- Run single test: `pytest tests/test_file.py::test_function`
- Test coverage: `pytest --cov=mcp_server_snowflake`

## Lint & Format
- Lint: `ruff check .`
- Format code: `ruff format .`
- Type check: `mypy mcp_server_snowflake/`

## Code Style Guidelines
- Use Python 3.10+ type annotations everywhere
- Format with Ruff, line length 88 characters
- Organize imports with Ruff (stdlib, third-party, first-party)
- Use async/await for Snowflake queries via snowflake-connector-python
- Prefer dataclasses or Pydantic models for structured data
- Follow PEP8 naming: snake_case for functions/variables, PascalCase for classes
- Document public functions with docstrings (Google style preferred)
- Handle database exceptions with proper logging and client-safe messages
- Parameterize all SQL queries to prevent injection vulnerabilities
- Use environment variables for configuration with pydantic-settings