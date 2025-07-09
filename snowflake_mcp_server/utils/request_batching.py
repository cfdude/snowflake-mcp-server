"""Request batching and throttling to prevent event listener accumulation.

This module provides request batching and throttling mechanisms to help prevent
the accumulation of event listeners on the MCP client side when multiple queries
are executed in rapid succession.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RequestThrottler:
    """Throttle requests to prevent overwhelming the client with responses."""
    
    def __init__(
        self,
        max_concurrent_requests: int = 5,
        min_request_interval_ms: int = 100,
        batch_window_ms: int = 500,
    ):
        """Initialize the throttler.
        
        Args:
            max_concurrent_requests: Maximum number of concurrent requests
            min_request_interval_ms: Minimum milliseconds between request starts
            batch_window_ms: Time window for batching similar requests
        """
        self.max_concurrent_requests = max_concurrent_requests
        self.min_request_interval_ms = min_request_interval_ms
        self.batch_window_ms = batch_window_ms
        
        self._active_requests = 0
        self._last_request_time = 0.0
        self._request_queue: Deque[Tuple[Callable, asyncio.Future]] = deque()
        self._batch_queue: Dict[str, List[asyncio.Future]] = {}
        self._lock = asyncio.Lock()
        self._processing_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the request processing task."""
        if self._processing_task is None or self._processing_task.done():
            self._processing_task = asyncio.create_task(self._process_requests())
            logger.info("Started request throttler")
    
    async def stop(self) -> None:
        """Stop the request processing task."""
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped request throttler")
    
    async def submit_request(
        self,
        request_func: Callable,
        batch_key: Optional[str] = None,
    ) -> Any:
        """Submit a request for throttled execution.
        
        Args:
            request_func: Async function to execute
            batch_key: Optional key for batching similar requests
            
        Returns:
            Result of the request function
        """
        future = asyncio.Future()
        
        async with self._lock:
            if batch_key and batch_key in self._batch_queue:
                # Add to existing batch
                self._batch_queue[batch_key].append(future)
                logger.debug(f"Added request to batch '{batch_key}' (size: {len(self._batch_queue[batch_key])})")
            else:
                # Create new request or batch
                self._request_queue.append((request_func, future))
                if batch_key:
                    self._batch_queue[batch_key] = [future]
        
        return await future
    
    async def _process_requests(self) -> None:
        """Process queued requests with throttling."""
        while True:
            try:
                # Wait if at capacity
                while self._active_requests >= self.max_concurrent_requests:
                    await asyncio.sleep(0.01)
                
                # Check for requests to process
                async with self._lock:
                    if not self._request_queue:
                        await asyncio.sleep(0.01)
                        continue
                    
                    # Enforce minimum interval between requests
                    current_time = time.time() * 1000  # Convert to milliseconds
                    time_since_last = current_time - self._last_request_time
                    if time_since_last < self.min_request_interval_ms:
                        await asyncio.sleep((self.min_request_interval_ms - time_since_last) / 1000)
                    
                    # Get next request
                    request_func, future = self._request_queue.popleft()
                    self._last_request_time = time.time() * 1000
                    self._active_requests += 1
                
                # Execute request
                asyncio.create_task(self._execute_request(request_func, future))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in request processor: {e}")
    
    async def _execute_request(self, request_func: Callable, future: asyncio.Future) -> None:
        """Execute a single request and update the future."""
        try:
            result = await request_func()
            if not future.done():
                future.set_result(result)
        except Exception as e:
            if not future.done():
                future.set_exception(e)
        finally:
            async with self._lock:
                self._active_requests -= 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get throttler statistics."""
        return {
            "active_requests": self._active_requests,
            "queued_requests": len(self._request_queue),
            "batch_count": len(self._batch_queue),
            "max_concurrent": self.max_concurrent_requests,
            "min_interval_ms": self.min_request_interval_ms,
        }


class ResponseBatcher:
    """Batch multiple query responses to reduce client-side processing overhead."""
    
    def __init__(self, batch_size: int = 10, batch_timeout_ms: int = 200):
        """Initialize the response batcher.
        
        Args:
            batch_size: Maximum number of responses to batch
            batch_timeout_ms: Maximum time to wait before sending a batch
        """
        self.batch_size = batch_size
        self.batch_timeout_ms = batch_timeout_ms
        
        self._batch: List[Dict[str, Any]] = []
        self._batch_start_time: Optional[float] = None
        self._lock = asyncio.Lock()
    
    async def add_response(self, response: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Add a response to the batch.
        
        Returns:
            Batched responses if batch is ready, None otherwise
        """
        async with self._lock:
            if not self._batch:
                self._batch_start_time = time.time() * 1000
            
            self._batch.append(response)
            
            # Check if batch is ready
            current_time = time.time() * 1000
            elapsed = current_time - self._batch_start_time if self._batch_start_time else 0
            
            if len(self._batch) >= self.batch_size or elapsed >= self.batch_timeout_ms:
                # Return batch
                batch = self._batch.copy()
                self._batch.clear()
                self._batch_start_time = None
                
                logger.debug(f"Sending batch of {len(batch)} responses")
                return batch
        
        return None
    
    async def flush(self) -> Optional[List[Dict[str, Any]]]:
        """Force flush any pending responses."""
        async with self._lock:
            if self._batch:
                batch = self._batch.copy()
                self._batch.clear()
                self._batch_start_time = None
                return batch
        return None


# Global instances
_request_throttler: Optional[RequestThrottler] = None
_response_batcher: Optional[ResponseBatcher] = None


def get_request_throttler() -> RequestThrottler:
    """Get or create the global request throttler."""
    global _request_throttler
    if _request_throttler is None:
        _request_throttler = RequestThrottler()
    return _request_throttler


def get_response_batcher() -> ResponseBatcher:
    """Get or create the global response batcher."""
    global _response_batcher
    if _response_batcher is None:
        _response_batcher = ResponseBatcher()
    return _response_batcher


async def start_request_management() -> None:
    """Start request throttling and batching."""
    throttler = get_request_throttler()
    await throttler.start()
    logger.info("Started request management (throttling and batching)")


async def stop_request_management() -> None:
    """Stop request throttling and batching."""
    throttler = get_request_throttler()
    await throttler.stop()
    
    # Flush any pending batches
    batcher = get_response_batcher()
    await batcher.flush()
    
    logger.info("Stopped request management")