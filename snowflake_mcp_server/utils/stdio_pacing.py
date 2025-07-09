"""Stdio pacing to prevent event listener accumulation in MCP clients.

This module provides a simple pacing mechanism for stdio-based MCP servers
to help prevent the accumulation of event listeners in Node.js-based MCP clients
when processing multiple requests in rapid succession.
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class StdioPacer:
    """Pace stdio responses to prevent client-side event listener accumulation."""
    
    def __init__(
        self,
        min_response_interval_ms: int = 50,
        adaptive: bool = True,
        max_interval_ms: int = 200,
    ):
        """Initialize the stdio pacer.
        
        Args:
            min_response_interval_ms: Minimum milliseconds between responses
            adaptive: Whether to adapt pacing based on request patterns
            max_interval_ms: Maximum interval when using adaptive pacing
        """
        self.min_response_interval_ms = min_response_interval_ms
        self.adaptive = adaptive
        self.max_interval_ms = max_interval_ms
        
        self._last_response_time: float = 0
        self._request_count = 0
        self._rapid_request_threshold = 5
        self._rapid_request_window_ms = 1000
        self._recent_requests: list[float] = []
        self._current_interval_ms = min_response_interval_ms
    
    async def pace_response(self) -> None:
        """Apply pacing before sending a response."""
        current_time = time.time() * 1000  # Convert to milliseconds
        
        # Track request patterns for adaptive pacing
        if self.adaptive:
            self._track_request_pattern(current_time)
            self._adjust_pacing()
        
        # Calculate time since last response
        time_since_last = current_time - self._last_response_time
        
        # Apply pacing if needed
        if time_since_last < self._current_interval_ms:
            delay_ms = self._current_interval_ms - time_since_last
            logger.debug(f"Pacing response by {delay_ms:.1f}ms (interval: {self._current_interval_ms}ms)")
            await asyncio.sleep(delay_ms / 1000)
        
        # Update last response time
        self._last_response_time = time.time() * 1000
        self._request_count += 1
    
    def _track_request_pattern(self, current_time: float) -> None:
        """Track request patterns for adaptive pacing."""
        # Remove old requests outside the window
        cutoff_time = current_time - self._rapid_request_window_ms
        self._recent_requests = [t for t in self._recent_requests if t > cutoff_time]
        
        # Add current request
        self._recent_requests.append(current_time)
    
    def _adjust_pacing(self) -> None:
        """Adjust pacing interval based on request patterns."""
        request_rate = len(self._recent_requests)
        
        if request_rate > self._rapid_request_threshold:
            # Increase pacing for rapid requests
            self._current_interval_ms = min(
                self._current_interval_ms * 1.5,
                self.max_interval_ms
            )
            logger.debug(
                f"Increased pacing interval to {self._current_interval_ms:.1f}ms "
                f"(request rate: {request_rate} requests/sec)"
            )
        elif request_rate < 2 and self._current_interval_ms > self.min_response_interval_ms:
            # Decrease pacing for slower requests
            self._current_interval_ms = max(
                self._current_interval_ms * 0.8,
                self.min_response_interval_ms
            )
            logger.debug(f"Decreased pacing interval to {self._current_interval_ms:.1f}ms")
    
    def get_stats(self) -> dict:
        """Get pacing statistics."""
        return {
            "total_requests": self._request_count,
            "current_interval_ms": self._current_interval_ms,
            "recent_request_count": len(self._recent_requests),
            "adaptive": self.adaptive,
        }


# Global pacer instance
_stdio_pacer: Optional[StdioPacer] = None


def get_stdio_pacer() -> StdioPacer:
    """Get or create the global stdio pacer."""
    global _stdio_pacer
    if _stdio_pacer is None:
        # Check environment for configuration
        import os
        min_interval = int(os.getenv("MCP_MIN_RESPONSE_INTERVAL_MS", "50"))
        adaptive = os.getenv("MCP_ADAPTIVE_PACING", "true").lower() == "true"
        max_interval = int(os.getenv("MCP_MAX_RESPONSE_INTERVAL_MS", "200"))
        
        _stdio_pacer = StdioPacer(
            min_response_interval_ms=min_interval,
            adaptive=adaptive,
            max_interval_ms=max_interval
        )
        logger.info(
            f"Initialized stdio pacer (min: {min_interval}ms, "
            f"max: {max_interval}ms, adaptive: {adaptive})"
        )
    
    return _stdio_pacer


async def pace_stdio_response() -> None:
    """Apply pacing before sending a stdio response."""
    pacer = get_stdio_pacer()
    await pacer.pace_response()