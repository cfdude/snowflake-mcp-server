"""Output handling utilities for query results with file and screen output support."""

import csv
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


class ResultOutputHandler:
    """Handles query result output based on configuration."""
    
    def __init__(self, config):
        """Initialize with OutputConfig."""
        self.config = config
        self.estimator = TokenEstimator(config)
        
    def generate_filename(self, format: str, query: Optional[str] = None) -> str:
        """Generate filename based on pattern."""
        now = datetime.now()
        
        # Get query hash if query provided
        query_hash = self.estimator.get_query_hash(query) if query else str(uuid.uuid4())[:8]
        
        replacements = {
            "{timestamp}": now.strftime("%Y%m%d_%H%M%S"),
            "{date}": now.strftime("%Y%m%d"),
            "{time}": now.strftime("%H%M%S"),
            "{query_hash}": query_hash
        }
        
        filename = self.config.filename_pattern
        for key, value in replacements.items():
            filename = filename.replace(key, value)
        
        # Add extension if not present
        if not filename.endswith(f".{format}"):
            filename = f"{filename}.{format}"
        
        return filename
    
    def resolve_output_path(self, location: Optional[str], filename: str) -> Path:
        """Resolve output path from configuration and parameters."""
        # Use provided location or default
        base_dir = location or self.config.default_output_dir
        
        # Handle relative vs absolute paths
        if os.path.isabs(base_dir):
            output_dir = Path(base_dir)
        else:
            # Relative to client project root if available
            if self.config.client_root:
                # Client root is provided - use it as the base for relative paths
                output_dir = Path(self.config.client_root) / base_dir
            else:
                # CRITICAL: If no client root is provided, we must refuse to write files
                # to avoid polluting the MCP server directory with client data
                raise ValueError(
                    "Cannot determine output location: MCP_CLIENT_ROOT environment variable not set. "
                    "Files cannot be written to the MCP server directory to avoid data pollution. "
                    "Please set MCP_CLIENT_ROOT to the calling project's root directory."
                )
        
        # Additional safety check: Never write to the MCP server's installation directory
        server_dir = Path(__file__).parent.parent.parent.resolve()
        resolved_output = output_dir.resolve()
        
        if resolved_output.is_relative_to(server_dir):
            raise ValueError(
                f"SECURITY ERROR: Attempted to write files to MCP server directory ({server_dir}). "
                f"Output directory ({resolved_output}) must be outside the server installation. "
                f"Please set MCP_CLIENT_ROOT environment variable to your project root."
            )
        
        # Create directory if it doesn't exist
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create output directory {output_dir}: {e}")
            raise ValueError(f"Cannot create output directory: {output_dir}")
        
        full_path = output_dir / filename
        
        # Validate write permissions
        try:
            # Test write access by creating a temporary file
            test_file = full_path.with_suffix('.tmp')
            test_file.touch()
            test_file.unlink()
        except OSError as e:
            logger.error(f"No write permission for {full_path}: {e}")
            raise ValueError(f"No write permission for output path: {full_path}")
        
        return full_path
    
    async def write_to_file(
        self,
        query: str,
        db_ops,
        format: str = "csv",
        location: Optional[str] = None,
        filename: Optional[str] = None,
        chunk_size: int = 10000
    ) -> Dict[str, Any]:
        """
        Stream query results to file.
        
        Args:
            query: SQL query to execute
            db_ops: Database operations instance
            format: Output format ("csv" or "json")
            location: Output directory
            filename: Output filename (auto-generated if None)
            chunk_size: Number of rows to process at once
            
        Returns:
            Dict with file information including path, size, rows written
        """
        # Generate filename if not provided
        if not filename:
            filename = self.generate_filename(format, query)
        
        # Resolve full output path
        output_path = self.resolve_output_path(location, filename)
        
        logger.info(f"Writing query results to {output_path} in {format.upper()} format")
        
        rows_written = 0
        columns = []
        
        try:
            # Execute query and stream results to file
            async with db_ops.cursor_manager.cursor() as cursor:
                # Execute the query
                await cursor.execute(query)
                
                # Get column information
                if cursor.cursor.description:
                    columns = [desc[0] for desc in cursor.cursor.description]
                else:
                    columns = []
                
                # Write to file based on format
                if format.lower() == "csv":
                    rows_written = await self._write_csv(cursor, output_path, columns, chunk_size)
                elif format.lower() == "json":
                    rows_written = await self._write_json(cursor, output_path, columns, chunk_size)
                else:
                    raise ValueError(f"Unsupported format: {format}")
            
            # Get actual file size from disk
            file_stats = os.stat(output_path)
            file_size_bytes = file_stats.st_size
            
            result = {
                "status": "success",
                "output": "file",
                "file_path": str(output_path.absolute()),
                "format": format,
                "rows_written": rows_written,
                "columns_written": len(columns),
                "file_size_bytes": file_size_bytes,
                "file_size_mb": round(file_size_bytes / (1024 * 1024), 2),
                "file_size_readable": self._format_bytes(file_size_bytes)
            }
            
            logger.info(f"Successfully wrote {rows_written:,} rows to {output_path} "
                       f"({result['file_size_readable']})")
            
            return result
            
        except Exception as e:
            logger.error(f"Error writing to file {output_path}: {e}")
            # Clean up partial file on error
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            raise
    
    async def _write_csv(self, cursor, output_path: Path, columns: List[str], chunk_size: int) -> int:
        """Write query results to CSV file."""
        rows_written = 0
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            
            # Write headers
            if columns:
                writer.writerow(columns)
            
            # Stream data in chunks
            while True:
                rows = await cursor.fetchmany(chunk_size)
                if not rows:
                    break
                
                # Process each row
                for row in rows:
                    # Convert None to empty string, handle other types
                    processed_row = []
                    for value in row:
                        if value is None:
                            processed_row.append("")
                        else:
                            # Convert to string, handle special cases
                            str_value = str(value)
                            processed_row.append(str_value)
                    
                    writer.writerow(processed_row)
                    rows_written += 1
        
        return rows_written
    
    async def _write_json(self, cursor, output_path: Path, columns: List[str], chunk_size: int) -> int:
        """Write query results to JSON file."""
        rows_written = 0
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('[\n')
            first_row = True
            
            # Stream data in chunks
            while True:
                rows = await cursor.fetchmany(chunk_size)
                if not rows:
                    break
                
                for row in rows:
                    if not first_row:
                        f.write(',\n')
                    first_row = False
                    
                    # Create row dictionary
                    if columns:
                        row_dict = {}
                        for col_name, value in zip(columns, row):
                            # Handle different data types for JSON serialization
                            if value is None:
                                row_dict[col_name] = None
                            elif isinstance(value, (datetime, )):
                                row_dict[col_name] = value.isoformat()
                            else:
                                row_dict[col_name] = value
                    else:
                        # No column names, use array
                        row_dict = list(row)
                    
                    json.dump(row_dict, f, default=str, ensure_ascii=False, indent=2)
                    rows_written += 1
            
            f.write('\n]')
        
        return rows_written
    
    def _format_bytes(self, bytes_count: int) -> str:
        """Format bytes into human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_count < 1024.0:
                return f"{bytes_count:.1f} {unit}"
            bytes_count /= 1024.0
        return f"{bytes_count:.1f} PB"
    
    def validate_output_parameters(
        self, 
        output: str, 
        format: str, 
        location: Optional[str], 
        filename: Optional[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate output parameters.
        
        Returns:
            (is_valid, error_message)
        """
        # Validate output mode
        if output not in ['auto', 'screen', 'file']:
            return False, "output must be 'auto', 'screen', or 'file'"
        
        # Validate format
        if format not in ['csv', 'json']:
            return False, "format must be 'csv' or 'json'"
        
        # Validate location if provided
        if location:
            try:
                test_path = Path(location)
                if test_path.is_file():
                    return False, f"location '{location}' is a file, not a directory"
            except (OSError, ValueError) as e:
                return False, f"Invalid location path: {location}"
        
        # Validate filename if provided
        if filename:
            try:
                # Check for invalid characters
                invalid_chars = '<>:"|?*'
                if any(char in filename for char in invalid_chars):
                    return False, f"filename contains invalid characters: {invalid_chars}"
                
                # Check for reserved names (Windows)
                reserved_names = ['CON', 'PRN', 'AUX', 'NUL'] + [f'COM{i}' for i in range(1, 10)] + [f'LPT{i}' for i in range(1, 10)]
                if filename.upper() in reserved_names:
                    return False, f"filename '{filename}' is a reserved name"
                
            except Exception as e:
                return False, f"Invalid filename: {filename}"
        
        return True, None