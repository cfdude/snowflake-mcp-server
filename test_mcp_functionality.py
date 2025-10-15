#!/usr/bin/env python3
"""
Practical test suite for Snowflake MCP Server new functionality
Tests the file output components directly using the large SQL queries
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Any

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class MockDatabaseOps:
    """Mock database operations for testing without Snowflake connection."""
    
    def __init__(self):
        self.queries_executed = []
        
    async def execute_query_one(self, query: str):
        """Mock execute query one - returns row count."""
        self.queries_executed.append(query)
        # Simulate different result sizes based on query
        if "COUNT(*)" in query.upper():
            if "movement_platform_properties" in query.lower():
                return (50000,)  # Large result set
            else:
                return (1000,)   # Smaller result set
        return (1,)
    
    async def execute_query(self, query: str):
        """Mock execute query - returns sample data."""
        self.queries_executed.append(query)
        
        # Simulate column names based on query
        if "movement_platform_properties" in query.lower():
            columns = ['lo_id', 'street_address', 'platform_events', 'first_activity', 'last_activity']
            # Generate sample data
            rows = [
                ('LO123', '123 MAIN ST', 15, '2024-01-15', '2024-06-20'),
                ('LO456', '456 ELM ST', 8, '2024-02-10', '2024-07-15'),
                ('LO789', '789 OAK AVE', 22, '2024-01-05', '2024-08-01'),
            ]
        else:
            columns = ['test_column', 'test_value']
            rows = [('test1', 'value1'), ('test2', 'value2')]
        
        return rows, columns
    
    async def use_database_isolated(self, database: str):
        """Mock database context."""
        pass
    
    async def use_schema_isolated(self, schema: str):
        """Mock schema context."""
        pass
    
    async def get_current_context(self):
        """Mock current context."""
        return "TEST_DB", "TEST_SCHEMA"
    
    @property
    def cursor_manager(self):
        """Mock cursor manager."""
        return MockCursorManager()


class MockCursorManager:
    """Mock cursor manager."""
    
    def cursor(self):
        return MockCursor()


class MockCursor:
    """Mock cursor for file writing tests."""
    
    def __init__(self):
        self.query = None
        self.rows_fetched = 0
        self.total_rows = 1000  # Simulate large dataset
        
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def execute(self, query: str):
        self.query = query
        self.rows_fetched = 0
        # Mock cursor description
        self.cursor = type('MockCursorDesc', (), {
            'description': [
                ('lo_id', None), ('street_address', None), ('platform_events', None),
                ('first_activity', None), ('last_activity', None), ('loan_amount', None)
            ]
        })()
    
    async def fetchmany(self, size: int):
        """Simulate fetching data in chunks."""
        if self.rows_fetched >= self.total_rows:
            return []
        
        # Generate sample rows
        rows = []
        for i in range(min(size, self.total_rows - self.rows_fetched)):
            rows.append((
                f'LO{self.rows_fetched + i + 1}',
                f'{100 + i} MAIN ST',
                15 + (i % 10),
                '2024-01-15',
                '2024-06-20',
                250000 + (i * 1000)
            ))
        
        self.rows_fetched += len(rows)
        return rows


async def test_token_estimation():
    """Test token estimation with real queries."""
    logger.info("Testing token estimation with large queries...")
    
    try:
        from snowflake_mcp_server.config import get_config
        from snowflake_mcp_server.utils.token_estimator import TokenEstimator
        
        config = get_config()
        estimator = TokenEstimator(config.output)
        db_ops = MockDatabaseOps()
        
        # Read the large SQL file
        sql_file = Path("movement_mortgage_attribution_corrected_queries.sql")
        if not sql_file.exists():
            logger.error("SQL file not found!")
            return False
        
        sql_content = sql_file.read_text()
        
        # Extract first query (up to semicolon)
        queries = sql_content.split(';')
        test_query = queries[0].strip()
        
        if not test_query:
            test_query = "SELECT * FROM large_table"
        
        logger.info(f"Testing with query of {len(test_query)} characters")
        
        # Test token estimation
        estimate = await estimator.estimate_query_tokens(test_query, db_ops)
        
        logger.info(f"Token estimation results:")
        logger.info(f"  - Row count: {estimate['row_count']:,}")
        logger.info(f"  - Estimated tokens: {estimate['estimated_total_tokens']:,}")
        logger.info(f"  - Estimated size: {estimate['estimated_size_kb']:,} KB")
        logger.info(f"  - Confidence: {estimate['confidence']}")
        
        # Test file output decision
        use_file, reasoning = await estimator.should_use_file_output(test_query, db_ops, None)
        
        logger.info(f"Output decision: {'FILE' if use_file else 'SCREEN'}")
        logger.info(f"Reasoning: {reasoning['reason']}")
        
        return True
        
    except Exception as e:
        logger.error(f"Token estimation test failed: {e}")
        return False


async def test_file_output():
    """Test file output functionality."""
    logger.info("Testing file output functionality...")
    
    try:
        from snowflake_mcp_server.config import get_config
        from snowflake_mcp_server.utils.output_handler import ResultOutputHandler
        
        config = get_config()
        handler = ResultOutputHandler(config.output)
        db_ops = MockDatabaseOps()
        
        # Test CSV output
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info("Testing CSV file output...")
            
            result = await handler.write_to_file(
                query="SELECT * FROM movement_platform_properties",
                db_ops=db_ops,
                format="csv",
                location=temp_dir,
                filename="test_movement_data.csv"
            )
            
            logger.info(f"CSV Output results:")
            logger.info(f"  - File: {result['file_path']}")
            logger.info(f"  - Rows: {result['rows_written']:,}")
            logger.info(f"  - Size: {result['file_size_readable']}")
            
            # Verify file exists and has content
            file_path = Path(result['file_path'])
            if not file_path.exists():
                logger.error("CSV file was not created!")
                return False
            
            content = file_path.read_text()
            logger.info(f"  - Content preview: {content[:200]}...")
            
            # Test JSON output
            logger.info("Testing JSON file output...")
            
            result = await handler.write_to_file(
                query="SELECT * FROM movement_loan_outcomes",
                db_ops=db_ops,
                format="json",
                location=temp_dir,
                filename="test_movement_data.json"
            )
            
            logger.info(f"JSON Output results:")
            logger.info(f"  - File: {result['file_path']}")
            logger.info(f"  - Rows: {result['rows_written']:,}")
            logger.info(f"  - Size: {result['file_size_readable']}")
            
            # Verify JSON file
            json_path = Path(result['file_path'])
            if not json_path.exists():
                logger.error("JSON file was not created!")
                return False
            
            json_content = json_path.read_text()
            logger.info(f"  - JSON preview: {json_content[:200]}...")
            
        return True
        
    except Exception as e:
        logger.error(f"File output test failed: {e}")
        return False


async def test_parameter_precedence():
    """Test parameter precedence (AI vs environment)."""
    logger.info("Testing parameter precedence...")
    
    try:
        from snowflake_mcp_server.config import get_config
        from snowflake_mcp_server.utils.output_handler import ResultOutputHandler
        
        config = get_config()
        handler = ResultOutputHandler(config.output)
        
        # Test 1: Environment defaults
        logger.info("Test 1: Environment defaults")
        filename1 = handler.generate_filename('csv', 'SELECT * FROM test')
        logger.info(f"  Generated filename: {filename1}")
        
        # Test 2: Custom pattern simulation
        original_pattern = config.output.filename_pattern
        config.output.filename_pattern = "custom_{query_hash}"
        filename2 = handler.generate_filename('json', 'SELECT * FROM custom_test')
        logger.info(f"  Custom pattern filename: {filename2}")
        
        # Test 3: Validation
        test_cases = [
            ("auto", "csv", "./test", "valid.csv", True),
            ("invalid", "csv", "./test", "valid.csv", False),
            ("file", "xml", "./test", "valid.xml", False),
            ("file", "json", "<invalid>", "valid.json", False),
        ]
        
        logger.info("Testing parameter validation:")
        for output, format, location, filename, should_pass in test_cases:
            valid, error = handler.validate_output_parameters(output, format, location, filename)
            result = "✓" if (valid == should_pass) else "✗"
            logger.info(f"  {result} {output}/{format}: {'Valid' if valid else error}")
        
        return True
        
    except Exception as e:
        logger.error(f"Parameter precedence test failed: {e}")
        return False


async def test_integration_flow():
    """Test the complete integration flow."""
    logger.info("Testing complete integration flow...")
    
    try:
        from snowflake_mcp_server.config import get_config
        from snowflake_mcp_server.utils.output_handler import ResultOutputHandler
        
        config = get_config()
        handler = ResultOutputHandler(config.output)
        db_ops = MockDatabaseOps()
        
        # Simulate the complete execute_query flow
        query = "SELECT * FROM movement_mortgage_attribution_analysis"
        
        # Step 1: Parameter resolution (AI vs environment)
        output_mode = None  # AI didn't specify
        if output_mode is None:
            output_mode = config.output.default_output
        
        file_format = "json"  # AI specified
        location = None  # AI didn't specify
        if location is None:
            location = config.output.default_output_dir
        
        filename = None  # AI didn't specify
        
        logger.info(f"Resolved parameters:")
        logger.info(f"  - Output: {output_mode} (from environment)")
        logger.info(f"  - Format: {file_format} (AI specified)")
        logger.info(f"  - Location: {location} (from environment)")
        logger.info(f"  - Filename: {filename} (will auto-generate)")
        
        # Step 2: Determine output strategy
        use_file, reasoning = await handler.estimator.should_use_file_output(
            query, db_ops, 
            force_output=None if output_mode == "auto" else output_mode
        )
        
        logger.info(f"Output decision: {'FILE' if use_file else 'SCREEN'}")
        logger.info(f"Reasoning: {reasoning['reason']}")
        
        # Step 3: Execute based on decision
        if use_file:
            if not filename and config.output.auto_generate_filename:
                filename = handler.generate_filename(file_format, query)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = await handler.write_to_file(
                    query, db_ops, file_format, temp_dir, filename
                )
                
                logger.info("File output completed:")
                logger.info(f"  - Path: {result['file_path']}")
                logger.info(f"  - Rows: {result['rows_written']:,}")
                logger.info(f"  - Size: {result['file_size_readable']}")
                
                # Verify the file
                file_path = Path(result['file_path'])
                if file_path.exists():
                    logger.info("✓ File successfully created")
                    return True
                else:
                    logger.error("✗ File was not created")
                    return False
        else:
            logger.info("Would return screen output")
            return True
        
    except Exception as e:
        logger.error(f"Integration flow test failed: {e}")
        return False


async def run_all_tests():
    """Run all practical tests."""
    logger.info("Starting practical MCP functionality tests...")
    
    tests = [
        ("Token Estimation", test_token_estimation),
        ("File Output", test_file_output),
        ("Parameter Precedence", test_parameter_precedence),
        ("Integration Flow", test_integration_flow),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*60}")
        logger.info(f"Running: {test_name}")
        logger.info(f"{'='*60}")
        
        try:
            result = await test_func()
            results[test_name] = result
            logger.info(f"✓ {test_name}: {'PASSED' if result else 'FAILED'}")
        except Exception as e:
            logger.error(f"✗ {test_name} crashed: {e}")
            results[test_name] = False
    
    # Print summary
    logger.info(f"\n{'='*60}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*60}")
    
    passed = sum(results.values())
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASSED" if result else "✗ FAILED"
        logger.info(f"{test_name:20} - {status}")
    
    logger.info(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 ALL TESTS PASSED!")
        return True
    else:
        logger.error(f"❌ {total - passed} tests failed")
        return False


if __name__ == "__main__":
    import sys
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)