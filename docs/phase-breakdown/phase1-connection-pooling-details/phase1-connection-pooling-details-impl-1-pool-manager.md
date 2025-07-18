# Phase 1: Connection Pooling Implementation Details

## Context & Overview

The current Snowflake MCP server uses a singleton connection pattern in `snowflake_mcp_server/utils/snowflake_conn.py` with a global `connection_manager` instance. This creates bottlenecks when multiple MCP clients (Claude Desktop, Claude Code, Roo Code) attempt concurrent database operations.

**Current Issues:**
- Single shared connection causes blocking between concurrent requests
- Thread-based locking reduces async performance benefits
- Connection refresh logic happens globally, affecting all clients
- Memory leaks possible due to shared connection state

**Target Architecture:**
- Async connection pool with configurable sizing
- Per-request connection acquisition/release
- Health monitoring with automatic connection replacement
- Proper connection lifecycle management

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "asyncpg>=0.28.0",  # For async connection utilities
    "asyncio-pool>=0.6.0",  # Connection pooling support
    "aiofiles>=23.2.0",  # Async file operations for key loading
]
```

## Implementation Plan

### 1. Pool Manager Implementation {#pool-manager}

Create new file: `snowflake_mcp_server/utils/async_pool.py`

```python
"""Async connection pool for Snowflake MCP server."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set
from contextlib import asynccontextmanager
import weakref

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import DatabaseError, OperationalError

from .snowflake_conn import SnowflakeConfig, get_snowflake_connection


logger = logging.getLogger(__name__)


class ConnectionPoolConfig:
    """Configuration for connection pool behavior."""
    
    def __init__(
        self,
        min_size: int = 2,
        max_size: int = 10,
        max_inactive_time: timedelta = timedelta(minutes=30),
        health_check_interval: timedelta = timedelta(minutes=5),
        connection_timeout: float = 30.0,
        retry_attempts: int = 3,
    ):
        self.min_size = min_size
        self.max_size = max_size
        self.max_inactive_time = max_inactive_time
        self.health_check_interval = health_check_interval
        self.connection_timeout = connection_timeout
        self.retry_attempts = retry_attempts


class PooledConnection:
    """Wrapper for pooled Snowflake connections with metadata."""
    
    def __init__(self, connection: SnowflakeConnection, pool: 'AsyncConnectionPool'):
        self.connection = connection
        self.pool_ref = weakref.ref(pool)
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.in_use = False
        self.health_checked_at = datetime.now()
        self.is_healthy = True
        self._lock = asyncio.Lock()
    
    async def mark_in_use(self) -> None:
        """Mark connection as in use."""
        async with self._lock:
            self.in_use = True
            self.last_used = datetime.now()
    
    async def mark_available(self) -> None:
        """Mark connection as available for reuse."""
        async with self._lock:
            self.in_use = False
            self.last_used = datetime.now()
    
    async def health_check(self) -> bool:
        """Perform health check on connection."""
        async with self._lock:
            try:
                # Simple health check query
                cursor = self.connection.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                
                self.is_healthy = True
                self.health_checked_at = datetime.now()
                return True
            except Exception as e:
                logger.warning(f"Connection health check failed: {e}")
                self.is_healthy = False
                return False
    
    def should_retire(self, max_inactive_time: timedelta) -> bool:
        """Check if connection should be retired due to inactivity."""
        return (
            not self.in_use and 
            datetime.now() - self.last_used > max_inactive_time
        )
    
    async def close(self) -> None:
        """Close the underlying connection."""
        try:
            self.connection.close()
        except Exception:
            pass  # Ignore errors during close


class AsyncConnectionPool:
    """Async connection pool for Snowflake connections."""
    
    def __init__(self, config: SnowflakeConfig, pool_config: ConnectionPoolConfig):
        self.snowflake_config = config
        self.pool_config = pool_config
        self._connections: Set[PooledConnection] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self._health_check_task: Optional[asyncio.Task] = None
    
    async def initialize(self) -> None:
        """Initialize the connection pool."""
        async with self._lock:
            # Create minimum number of connections
            for _ in range(self.pool_config.min_size):
                try:
                    await self._create_connection()
                except Exception as e:
                    logger.error(f"Failed to create initial connection: {e}")
            
            # Start health check task
            self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def _create_connection(self) -> PooledConnection:
        """Create a new pooled connection."""
        # Convert sync connection creation to async
        loop = asyncio.get_event_loop()
        connection = await loop.run_in_executor(
            None, get_snowflake_connection, self.snowflake_config
        )
        
        pooled_conn = PooledConnection(connection, self)
        self._connections.add(pooled_conn)
        logger.debug(f"Created new connection. Pool size: {len(self._connections)}")
        return pooled_conn
    
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        
        connection = await self._get_connection()
        try:
            await connection.mark_in_use()
            yield connection.connection
        finally:
            await connection.mark_available()
    
    async def _get_connection(self) -> PooledConnection:
        """Get an available connection from the pool."""
        async with self._lock:
            # Find available healthy connection
            for conn in self._connections:
                if not conn.in_use and conn.is_healthy:
                    return conn
            
            # Create new connection if under max size
            if len(self._connections) < self.pool_config.max_size:
                return await self._create_connection()
            
            # Wait for connection to become available
            while True:
                await asyncio.sleep(0.1)  # Small delay
                for conn in self._connections:
                    if not conn.in_use and conn.is_healthy:
                        return conn
    
    async def _health_check_loop(self) -> None:
        """Background task for connection health checking."""
        while not self._closed:
            try:
                await asyncio.sleep(self.pool_config.health_check_interval.total_seconds())
                await self._perform_health_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _perform_health_checks(self) -> None:
        """Perform health checks and cleanup on all connections."""
        async with self._lock:
            connections_to_remove = set()
            
            for conn in self._connections.copy():
                # Check if connection should be retired
                if conn.should_retire(self.pool_config.max_inactive_time):
                    connections_to_remove.add(conn)
                    continue
                
                # Perform health check on idle connections
                if not conn.in_use:
                    is_healthy = await conn.health_check()
                    if not is_healthy:
                        connections_to_remove.add(conn)
            
            # Remove unhealthy/retired connections
            for conn in connections_to_remove:
                self._connections.discard(conn)
                await conn.close()
            
            # Ensure minimum pool size
            while len(self._connections) < self.pool_config.min_size:
                try:
                    await self._create_connection()
                except Exception as e:
                    logger.error(f"Failed to maintain minimum pool size: {e}")
                    break
    
    async def close(self) -> None:
        """Close the connection pool and all connections."""
        self._closed = True
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            for conn in self._connections:
                await conn.close()
            self._connections.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        total_connections = len(self._connections)
        active_connections = sum(1 for conn in self._connections if conn.in_use)
        healthy_connections = sum(1 for conn in self._connections if conn.is_healthy)
        
        return {
            "total_connections": total_connections,
            "active_connections": active_connections,
            "available_connections": total_connections - active_connections,
            "healthy_connections": healthy_connections,
            "pool_config": {
                "min_size": self.pool_config.min_size,
                "max_size": self.pool_config.max_size,
                "max_inactive_time_minutes": self.pool_config.max_inactive_time.total_seconds() / 60,
            }
        }


# Global pool instance
_pool: Optional[AsyncConnectionPool] = None
_pool_lock = asyncio.Lock()


async def get_connection_pool() -> AsyncConnectionPool:
    """Get the global connection pool instance."""
    global _pool
    if _pool is None:
        raise RuntimeError("Connection pool not initialized")
    return _pool


async def initialize_connection_pool(
    snowflake_config: SnowflakeConfig,
    pool_config: Optional[ConnectionPoolConfig] = None
) -> None:
    """Initialize the global connection pool."""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
        
        if pool_config is None:
            pool_config = ConnectionPoolConfig()
        
        _pool = AsyncConnectionPool(snowflake_config, pool_config)
        await _pool.initialize()


async def close_connection_pool() -> None:
    """Close the global connection pool."""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None
```

