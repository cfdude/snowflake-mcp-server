module.exports = {
  apps: [
    {
      // Snowflake MCP Server - HTTP mode (PM2 managed, shared by Claude Code)
      name: 'snowflake-mcp-http',
      script: 'uv',
      args: 'run snowflake-mcp-http',
      cwd: '/Users/robsherman/Servers/snowflake-mcp-server',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      env: {
        NODE_ENV: 'production',
        PYTHONPATH: '/Users/robsherman/Servers/snowflake-mcp-server',
        SNOWFLAKE_CONN_REFRESH_HOURS: '8',
      },
      // Logging
      log_file: './logs/snowflake-mcp-http.log',
      error_file: './logs/snowflake-mcp-http-error.log',
      out_file: './logs/snowflake-mcp-http-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,

      // Auto-restart
      autorestart: true,
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',

      // Memory limit
      max_memory_restart: '300M',

      // Health monitoring
      health_check_url: 'http://localhost:8000/health',
      health_check_grace_period: 10000,

      // Process management
      kill_timeout: 5000,
      listen_timeout: 8000,
      exp_backoff_restart_delay: 100,
    },
  ],
};
