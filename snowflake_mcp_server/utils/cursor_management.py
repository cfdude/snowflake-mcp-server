"""Enhanced cursor management with lifecycle tracking and cleanup.

This module provides improved cursor management to help diagnose and prevent
resource leaks that might contribute to client-side event listener accumulation.
"""

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, Set

from snowflake.connector import SnowflakeConnection

logger = logging.getLogger(__name__)


class ManagedCursor:
    """Wrapper for Snowflake cursor with lifecycle tracking."""
    
    _active_cursors: Set[weakref.ref] = set()
    _cursor_count = 0
    
    def __init__(self, cursor: Any, connection_id: str):
        """Initialize managed cursor."""
        self.cursor = cursor
        self.connection_id = connection_id
        self.created_at = datetime.now()
        self.closed_at: Optional[datetime] = None
        self.query_count = 0
        
        # Track cursor ID
        ManagedCursor._cursor_count += 1
        self.cursor_id = f"cursor_{ManagedCursor._cursor_count}"
        
        # Add to active cursors tracking
        self._ref = weakref.ref(self, ManagedCursor._on_cursor_deleted)
        ManagedCursor._active_cursors.add(self._ref)
        
        logger.debug(
            f"Created cursor {self.cursor_id} on connection {connection_id}. "
            f"Active cursors: {len(ManagedCursor._active_cursors)}"
        )
    
    @classmethod
    def _on_cursor_deleted(cls, ref: weakref.ref) -> None:
        """Callback when cursor is garbage collected."""
        cls._active_cursors.discard(ref)
        logger.debug(f"Cursor garbage collected. Active cursors: {len(cls._active_cursors)}")
    
    async def execute(self, query: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Execute query with tracking."""
        self.query_count += 1
        logger.debug(f"Cursor {self.cursor_id} executing query #{self.query_count}")
        
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        if params:
            return await loop.run_in_executor(None, self.cursor.execute, query, params)
        else:
            return await loop.run_in_executor(None, self.cursor.execute, query)
    
    async def fetchall(self) -> Any:
        """Fetch all results."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.cursor.fetchall)
    
    async def fetchone(self) -> Any:
        """Fetch one result."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.cursor.fetchone)
    
    async def fetchmany(self, size: int) -> Any:
        """Fetch many results."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.cursor.fetchmany, size)
    
    async def close(self) -> None:
        """Close cursor with tracking."""
        if self.closed_at is not None:
            logger.warning(f"Cursor {self.cursor_id} already closed")
            return
        
        self.closed_at = datetime.now()
        duration = (self.closed_at - self.created_at).total_seconds()
        
        try:
            self.cursor.close()
            logger.debug(
                f"Closed cursor {self.cursor_id} after {duration:.2f}s "
                f"and {self.query_count} queries. "
                f"Active cursors: {len(ManagedCursor._active_cursors) - 1}"
            )
        except Exception as e:
            logger.error(f"Error closing cursor {self.cursor_id}: {e}")
    
    @classmethod
    def get_active_cursor_count(cls) -> int:
        """Get count of active cursors."""
        # Clean up dead references
        dead_refs = [ref for ref in cls._active_cursors if ref() is None]
        for ref in dead_refs:
            cls._active_cursors.discard(ref)
        
        return len(cls._active_cursors)
    
    @classmethod
    def get_cursor_stats(cls) -> Dict[str, Any]:
        """Get cursor statistics."""
        active_count = cls.get_active_cursor_count()
        return {
            "active_cursors": active_count,
            "total_created": cls._cursor_count,
            "leaked_cursors": active_count  # Cursors that weren't properly closed
        }


@asynccontextmanager
async def get_managed_cursor(connection: SnowflakeConnection, connection_id: str) -> Any:
    """Get a managed cursor with automatic cleanup and tracking."""
    cursor = None
    managed_cursor = None
    
    try:
        # Create cursor synchronously in executor
        loop = asyncio.get_event_loop()
        cursor = await loop.run_in_executor(None, connection.cursor)
        
        # Wrap in managed cursor
        managed_cursor = ManagedCursor(cursor, connection_id)
        
        yield managed_cursor
        
    finally:
        # Ensure cursor is closed
        if managed_cursor is not None:
            await managed_cursor.close()
        elif cursor is not None:
            # Fallback if managed cursor creation failed
            try:
                cursor.close()
            except Exception as e:
                logger.error(f"Error closing raw cursor: {e}")


def log_cursor_metrics() -> None:
    """Log current cursor metrics."""
    stats = ManagedCursor.get_cursor_stats()
    logger.info(
        f"Cursor metrics - Active: {stats['active_cursors']}, "
        f"Total created: {stats['total_created']}, "
        f"Potential leaks: {stats['leaked_cursors']}"
    )