"""Connection pool metrics and monitoring.

This module provides real-time metrics and monitoring capabilities for the
connection pool to help diagnose issues that might lead to event listener
accumulation on the client side.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .async_pool import get_connection_pool
from .cursor_management import ManagedCursor

logger = logging.getLogger(__name__)


class ConnectionPoolMetrics:
    """Tracks and reports connection pool metrics."""
    
    def __init__(self):
        """Initialize metrics tracking."""
        self.metrics_history: List[Dict[str, Any]] = []
        self.max_history_size = 100
        self.start_time = datetime.now()
        self._metrics_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
    
    async def start_monitoring(self, interval_seconds: int = 30) -> None:
        """Start periodic metrics collection."""
        if self._metrics_task and not self._metrics_task.done():
            logger.warning("Metrics monitoring already running")
            return
        
        self._stop_event.clear()
        self._metrics_task = asyncio.create_task(
            self._collect_metrics_periodically(interval_seconds)
        )
        logger.info(f"Started connection pool metrics monitoring (interval: {interval_seconds}s)")
    
    async def stop_monitoring(self) -> None:
        """Stop metrics collection."""
        self._stop_event.set()
        if self._metrics_task:
            try:
                await asyncio.wait_for(self._metrics_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Metrics task did not complete in time")
            self._metrics_task = None
        logger.info("Stopped connection pool metrics monitoring")
    
    async def _collect_metrics_periodically(self, interval_seconds: int) -> None:
        """Periodically collect and log metrics."""
        while not self._stop_event.is_set():
            try:
                metrics = await self.collect_current_metrics()
                self._store_metrics(metrics)
                self._log_metrics_summary(metrics)
                
                # Check for potential issues
                self._check_for_issues(metrics)
                
            except Exception as e:
                logger.error(f"Error collecting metrics: {e}")
            
            # Wait for next collection
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), 
                    timeout=interval_seconds
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                continue  # Continue monitoring
    
    async def collect_current_metrics(self) -> Dict[str, Any]:
        """Collect current connection pool and cursor metrics."""
        try:
            pool = await get_connection_pool()
            pool_stats = pool.get_stats()
            
            # Add cursor metrics
            cursor_stats = ManagedCursor.get_cursor_stats()
            
            # Calculate derived metrics
            uptime = (datetime.now() - self.start_time).total_seconds()
            
            return {
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": uptime,
                "pool": {
                    "total_connections": pool_stats["total_connections"],
                    "active_connections": pool_stats["active_connections"],
                    "available_connections": pool_stats["available_connections"],
                    "healthy_connections": pool_stats["healthy_connections"],
                    "utilization_percent": (
                        pool_stats["active_connections"] / pool_stats["total_connections"] * 100
                        if pool_stats["total_connections"] > 0 else 0
                    ),
                },
                "cursors": cursor_stats,
                "config": pool_stats["pool_config"],
            }
        except Exception as e:
            logger.error(f"Failed to collect metrics: {e}")
            return {
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }
    
    def _store_metrics(self, metrics: Dict[str, Any]) -> None:
        """Store metrics in history."""
        self.metrics_history.append(metrics)
        
        # Maintain max history size
        if len(self.metrics_history) > self.max_history_size:
            self.metrics_history = self.metrics_history[-self.max_history_size:]
    
    def _log_metrics_summary(self, metrics: Dict[str, Any]) -> None:
        """Log a summary of current metrics."""
        if "error" in metrics:
            logger.error(f"Metrics collection error: {metrics['error']}")
            return
        
        pool_metrics = metrics.get("pool", {})
        cursor_metrics = metrics.get("cursors", {})
        
        logger.info(
            f"Connection Pool Status - "
            f"Connections: {pool_metrics.get('active_connections', 0)}/"
            f"{pool_metrics.get('total_connections', 0)} active "
            f"({pool_metrics.get('utilization_percent', 0):.1f}% utilized), "
            f"Cursors: {cursor_metrics.get('active_cursors', 0)} active, "
            f"{cursor_metrics.get('total_created', 0)} total created"
        )
    
    def _check_for_issues(self, metrics: Dict[str, Any]) -> None:
        """Check for potential issues and log warnings."""
        if "error" in metrics:
            return
        
        pool_metrics = metrics.get("pool", {})
        cursor_metrics = metrics.get("cursors", {})
        
        # Check for high connection utilization
        utilization = pool_metrics.get("utilization_percent", 0)
        if utilization > 80:
            logger.warning(
                f"High connection pool utilization: {utilization:.1f}%. "
                "Consider increasing pool size."
            )
        
        # Check for cursor leaks
        leaked_cursors = cursor_metrics.get("leaked_cursors", 0)
        if leaked_cursors > 10:
            logger.warning(
                f"Potential cursor leak detected: {leaked_cursors} cursors not properly closed"
            )
        
        # Check for unhealthy connections
        total_conns = pool_metrics.get("total_connections", 0)
        healthy_conns = pool_metrics.get("healthy_connections", 0)
        if total_conns > 0 and healthy_conns < total_conns * 0.5:
            logger.warning(
                f"Many unhealthy connections: {healthy_conns}/{total_conns} healthy"
            )
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of recent metrics."""
        if not self.metrics_history:
            return {"status": "no_data"}
        
        recent_metrics = self.metrics_history[-10:]  # Last 10 samples
        
        # Calculate averages
        avg_active_conns = sum(
            m.get("pool", {}).get("active_connections", 0) 
            for m in recent_metrics
        ) / len(recent_metrics)
        
        avg_utilization = sum(
            m.get("pool", {}).get("utilization_percent", 0) 
            for m in recent_metrics
        ) / len(recent_metrics)
        
        max_active_cursors = max(
            m.get("cursors", {}).get("active_cursors", 0) 
            for m in recent_metrics
        )
        
        return {
            "status": "ok",
            "samples": len(recent_metrics),
            "averages": {
                "active_connections": avg_active_conns,
                "utilization_percent": avg_utilization,
            },
            "peaks": {
                "max_active_cursors": max_active_cursors,
            },
            "latest": recent_metrics[-1] if recent_metrics else None,
        }
    
    def export_metrics(self, filepath: str) -> None:
        """Export metrics history to file."""
        try:
            with open(filepath, 'w') as f:
                json.dump(self.metrics_history, f, indent=2)
            logger.info(f"Exported {len(self.metrics_history)} metrics samples to {filepath}")
        except Exception as e:
            logger.error(f"Failed to export metrics: {e}")


# Global metrics instance
_metrics_collector: Optional[ConnectionPoolMetrics] = None


def get_metrics_collector() -> ConnectionPoolMetrics:
    """Get or create the global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = ConnectionPoolMetrics()
    return _metrics_collector


async def start_connection_monitoring(interval_seconds: int = 30) -> None:
    """Start connection pool monitoring."""
    collector = get_metrics_collector()
    await collector.start_monitoring(interval_seconds)


async def stop_connection_monitoring() -> None:
    """Stop connection pool monitoring."""
    collector = get_metrics_collector()
    await collector.stop_monitoring()


async def get_connection_metrics_summary() -> Dict[str, Any]:
    """Get current connection metrics summary."""
    collector = get_metrics_collector()
    return collector.get_metrics_summary()