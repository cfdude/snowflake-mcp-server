# MaxListenersExceededWarning Solution Guide

## Problem Description

When executing multiple queries in rapid succession through the Snowflake MCP server, you may encounter the following Node.js warning:

```
(node:55163) MaxListenersExceededWarning: Possible EventTarget memory leak detected. 11 abort listeners added to [AbortSignal]. Use events.setMaxListeners() to increase limit
```

This warning originates from the MCP client (Claude Desktop), not the Python server, and indicates that event listeners are accumulating without proper cleanup.

## Root Cause Analysis

The warning occurs because:

1. **Client-Side Event Accumulation**: The MCP JavaScript/Node.js client creates AbortSignal listeners for each request
2. **Rapid Query Execution**: Multiple queries executed in quick succession can cause listeners to accumulate faster than they're cleaned up
3. **Default Node.js Limit**: Node.js warns when more than 10 listeners are attached to a single EventEmitter

## Solution Implementation

We've implemented several server-side improvements to help mitigate this issue:

### 1. Enhanced Cursor Management (`cursor_management.py`)

- **Lifecycle Tracking**: Tracks cursor creation, usage, and cleanup
- **Automatic Cleanup**: Ensures cursors are properly closed after use
- **Leak Detection**: Monitors for cursors that aren't properly closed
- **Metrics Collection**: Provides visibility into cursor usage patterns

### 2. Connection Pool Monitoring (`connection_metrics.py`)

- **Real-time Metrics**: Monitors pool health, utilization, and connection status
- **Issue Detection**: Automatically detects high utilization or connection leaks
- **Performance Tracking**: Logs metrics to help identify bottlenecks

### 3. Request Throttling and Batching (`request_batching.py`)

- **Request Throttling**: Limits concurrent requests to prevent overwhelming the client
- **Response Batching**: Groups multiple responses to reduce processing overhead
- **Configurable Limits**: Adjustable parameters for different workload patterns

## Configuration Options

### Environment Variables

```bash
# Connection Pool Configuration
SNOWFLAKE_POOL_MIN_SIZE=2
SNOWFLAKE_POOL_MAX_SIZE=10
SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES=30
SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES=5

# Request Throttling (if implemented)
MCP_MAX_CONCURRENT_REQUESTS=5
MCP_MIN_REQUEST_INTERVAL_MS=100
MCP_BATCH_WINDOW_MS=500
```

## Usage Recommendations

### 1. Enable Monitoring

Add to your server initialization:

```python
from snowflake_mcp_server.utils.connection_metrics import start_connection_monitoring
from snowflake_mcp_server.utils.request_batching import start_request_management

# In your startup code
await start_connection_monitoring(interval_seconds=30)
await start_request_management()
```

### 2. Query Execution Best Practices

- **Batch Related Queries**: Group related queries into single operations when possible
- **Add Small Delays**: For non-critical queries, add small delays between executions
- **Use Connection Pooling**: Leverage the async connection pool for better resource management

### 3. Monitor Metrics

Check connection and cursor metrics periodically:

```python
from snowflake_mcp_server.utils.connection_metrics import get_connection_metrics_summary
from snowflake_mcp_server.utils.cursor_management import ManagedCursor

# Get current metrics
metrics = await get_connection_metrics_summary()
cursor_stats = ManagedCursor.get_cursor_stats()
```

## Client-Side Workarounds

While the server-side improvements help, you may also need client-side adjustments:

### 1. Increase Node.js Event Listener Limit

In your Claude Desktop configuration or startup script:

```javascript
// Increase the default limit
require('events').EventEmitter.defaultMaxListeners = 20;

// Or for specific emitters
emitter.setMaxListeners(20);
```

### 2. Implement Query Queuing

Instead of firing all queries simultaneously, queue them:

```javascript
async function executeQueriesSequentially(queries) {
  const results = [];
  for (const query of queries) {
    const result = await executeQuery(query);
    results.push(result);
    // Small delay between queries
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return results;
}
```

## Monitoring and Debugging

### Enable Debug Logging

```bash
# Set logging level
export SNOWFLAKE_MCP_LOG_LEVEL=DEBUG

# Enable connection tracking
export SNOWFLAKE_MCP_TRACK_CONNECTIONS=true
```

### Check Metrics Endpoint

The server provides metrics that can help diagnose issues:

```python
# In your server code
@server.tool()
async def get_server_metrics():
    """Get current server metrics including connections and cursors."""
    from snowflake_mcp_server.utils.connection_metrics import get_connection_metrics_summary
    from snowflake_mcp_server.utils.cursor_management import ManagedCursor
    
    metrics = await get_connection_metrics_summary()
    cursor_stats = ManagedCursor.get_cursor_stats()
    
    return {
        "connection_pool": metrics,
        "cursors": cursor_stats,
        "timestamp": datetime.now().isoformat()
    }
```

## Future Improvements

1. **WebSocket Support**: Implement WebSocket transport for better connection management
2. **Query Result Streaming**: Stream large results to reduce memory pressure
3. **Client Library Updates**: Work with MCP team to improve client-side event management
4. **Automatic Retry Logic**: Implement smart retry with exponential backoff

## Troubleshooting Steps

If you continue to see the warning:

1. **Check Query Patterns**: Log and analyze your query execution patterns
2. **Monitor Resource Usage**: Use the provided metrics to identify bottlenecks
3. **Adjust Pool Settings**: Tune connection pool size based on workload
4. **Enable Throttling**: Use request throttling for high-volume scenarios
5. **Report to MCP Team**: If the issue persists, report to the MCP/Claude Desktop team

## Example Implementation

```python
# In your query handler
from snowflake_mcp_server.utils.request_batching import get_request_throttler

async def handle_query_with_throttling(query: str):
    throttler = get_request_throttler()
    
    async def execute():
        # Your existing query logic
        return await execute_query(query)
    
    # Submit through throttler
    return await throttler.submit_request(execute)
```

This solution provides multiple layers of protection against resource exhaustion while maintaining query performance and reliability.