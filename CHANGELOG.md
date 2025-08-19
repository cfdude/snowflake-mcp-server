# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2025-08-19

### Added
- **File Output Support**: Intelligent handling of large query results that exceed AI model token limits
  - Automatic token estimation based on query result size and model capabilities
  - Smart output routing: screen display for small results, file output for large datasets
  - Support for CSV and JSON output formats
  - Configurable output directories and filename patterns
  - Real file size reporting from disk after write completion

- **Token-Aware Decision Making**: 
  - Support for major AI models (Claude, GPT, Gemini, etc.) with configurable token limits
  - Safety margins to prevent context overflow
  - Intelligent estimation based on column count and data complexity
  - Source: https://llm-stats.com for up-to-date model capabilities

- **Enhanced MCP Tool Parameters**:
  - `output`: Control output mode (auto/screen/file)
  - `format`: Choose file format (csv/json) 
  - `location`: Specify output directory
  - `filename`: Custom filename or auto-generation

- **Configuration Management**:
  - Environment-based configuration for model settings
  - Parameter precedence: AI explicit choices override environment defaults
  - Comprehensive validation and error handling

### Changed
- **execute_query tool**: Enhanced with file output capabilities while maintaining backward compatibility
- **.env.example**: Added comprehensive output configuration examples and model reference data

### Technical
- New modules: `token_estimator.py` and `output_handler.py` 
- Streaming file output for memory efficiency with large datasets
- Enhanced configuration system with `OutputConfig` class
- Comprehensive test coverage for new functionality

## [0.2.0] - Previous Release
- Async connection pooling and isolation
- Multi-client support
- HTTP server transport
- Security enhancements

## [0.1.0] - Initial Release
- Basic MCP server functionality
- Snowflake read-only operations
- stdio transport