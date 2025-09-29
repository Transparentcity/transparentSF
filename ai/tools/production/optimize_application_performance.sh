#!/bin/bash

# Script to optimize TransparentSF application performance and reduce timeout issues
# This script implements several optimizations to prevent 504 Gateway Timeout errors

echo "🔧 Optimizing TransparentSF application performance..."

# Set the application directory
APP_DIR="/opt/transparentsf/ai"
cd "$APP_DIR" || { echo "❌ Cannot access application directory"; exit 1; }

echo "📁 Working in directory: $APP_DIR"

# 1. Optimize database connection settings
echo "🗄️ Optimizing database connection settings..."

# Create optimized database configuration
cat > tools/db_optimization.py << 'DB_OPT_EOF'
"""
Database optimization settings for production
"""
import os
import psycopg2
from psycopg2 import pool

# Database connection pool settings for better performance
DB_POOL_SETTINGS = {
    'minconn': 5,      # Minimum connections in pool
    'maxconn': 20,     # Maximum connections in pool
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': os.getenv('POSTGRES_PORT', '5432'),
    'database': os.getenv('POSTGRES_DB', 'transparentsf'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', ''),
    'connect_timeout': 30,
    'application_name': 'transparentsf_optimized'
}

# Query timeout settings (in seconds)
QUERY_TIMEOUTS = {
    'fast_queries': 30,      # Simple queries
    'medium_queries': 120,   # Complex queries
    'heavy_queries': 600,    # Very complex queries (10 minutes)
    'background_jobs': 1800  # Background operations (30 minutes)
}

def get_optimized_connection():
    """Get an optimized database connection with proper settings."""
    try:
        conn = psycopg2.connect(**DB_POOL_SETTINGS)
        
        # Set connection-level optimizations
        with conn.cursor() as cursor:
            # Set statement timeout for this connection
            cursor.execute("SET statement_timeout = '10min'")
            
            # Optimize for read-heavy workload
            cursor.execute("SET default_transaction_isolation = 'read committed'")
            
            # Enable query planning optimizations
            cursor.execute("SET random_page_cost = 1.1")
            cursor.execute("SET effective_cache_size = '1GB'")
            
        return conn
    except Exception as e:
        print(f"Error creating optimized connection: {e}")
        return None

# Export settings for use in other modules
__all__ = ['DB_POOL_SETTINGS', 'QUERY_TIMEOUTS', 'get_optimized_connection']
DB_OPT_EOF

echo "✅ Database optimization settings created"

# 2. Create query optimization utilities
echo "🔍 Creating query optimization utilities..."

cat > tools/query_optimizer.py << 'QUERY_OPT_EOF'
"""
Query optimization utilities for TransparentSF
"""
import logging
import time
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

def optimize_query(timeout_seconds: int = 120, max_rows: int = 100000):
    """
    Decorator to optimize database queries with timeout and row limits.
    
    Args:
        timeout_seconds: Maximum time allowed for query execution
        max_rows: Maximum number of rows to return
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                # Add timeout and row limit to kwargs if not present
                if 'timeout' not in kwargs:
                    kwargs['timeout'] = timeout_seconds
                if 'limit' not in kwargs:
                    kwargs['limit'] = max_rows
                
                result = func(*args, **kwargs)
                
                execution_time = time.time() - start_time
                logger.info(f"Query {func.__name__} completed in {execution_time:.2f}s")
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"Query {func.__name__} failed after {execution_time:.2f}s: {e}")
                raise
                
        return wrapper
    return decorator

def add_query_hints(query: str, hints: Optional[dict] = None) -> str:
    """
    Add PostgreSQL query hints for optimization.
    
    Args:
        query: SQL query string
        hints: Dictionary of optimization hints
    
    Returns:
        Optimized query string
    """
    if not hints:
        hints = {}
    
    # Add common optimizations
    optimized_query = query
    
    # Add LIMIT if not present and we have a row limit hint
    if 'max_rows' in hints and 'LIMIT' not in query.upper():
        optimized_query += f" LIMIT {hints['max_rows']}"
    
    # Add ORDER BY optimization hints
    if 'order_by' in hints and 'ORDER BY' not in query.upper():
        optimized_query += f" ORDER BY {hints['order_by']}"
    
    return optimized_query

def log_slow_queries(threshold_seconds: float = 5.0):
    """
    Decorator to log queries that take longer than threshold.
    
    Args:
        threshold_seconds: Time threshold for logging slow queries
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            
            if execution_time > threshold_seconds:
                logger.warning(f"SLOW QUERY: {func.__name__} took {execution_time:.2f}s")
            
            return result
        return wrapper
    return decorator

# Export utilities
__all__ = ['optimize_query', 'add_query_hints', 'log_slow_queries']
QUERY_OPT_EOF

echo "✅ Query optimization utilities created"

# 3. Create performance monitoring script
echo "📊 Creating performance monitoring script..."

cat > tools/performance_monitor.py << 'PERF_MON_EOF'
"""
Performance monitoring utilities for TransparentSF
"""
import logging
import time
import psutil
import os
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PerformanceMonitor:
    """Monitor application performance metrics."""
    
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.slow_requests = []
    
    def log_request(self, endpoint: str, duration: float, status_code: int = 200):
        """Log a request and its performance metrics."""
        self.request_count += 1
        
        # Log slow requests (> 10 seconds)
        if duration > 10.0:
            self.slow_requests.append({
                'endpoint': endpoint,
                'duration': duration,
                'status_code': status_code,
                'timestamp': datetime.now().isoformat()
            })
            logger.warning(f"SLOW REQUEST: {endpoint} took {duration:.2f}s")
        
        # Log very slow requests (> 30 seconds)
        if duration > 30.0:
            logger.error(f"VERY SLOW REQUEST: {endpoint} took {duration:.2f}s")
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get current system performance metrics."""
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory usage
            memory = psutil.virtual_memory()
            
            # Disk usage
            disk = psutil.disk_usage('/')
            
            # Process info
            process = psutil.Process(os.getpid())
            process_memory = process.memory_info()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available_gb': memory.available / (1024**3),
                'disk_percent': disk.percent,
                'disk_free_gb': disk.free / (1024**3),
                'process_memory_mb': process_memory.rss / (1024**2),
                'uptime_seconds': time.time() - self.start_time,
                'request_count': self.request_count,
                'slow_requests_count': len(self.slow_requests)
            }
        except Exception as e:
            logger.error(f"Error getting system metrics: {e}")
            return {}
    
    def log_performance_summary(self):
        """Log a performance summary."""
        metrics = self.get_system_metrics()
        logger.info(f"Performance Summary: {metrics}")
        
        if self.slow_requests:
            logger.warning(f"Found {len(self.slow_requests)} slow requests")
            for req in self.slow_requests[-5:]:  # Show last 5 slow requests
                logger.warning(f"  - {req['endpoint']}: {req['duration']:.2f}s")

# Global performance monitor instance
perf_monitor = PerformanceMonitor()

# Export
__all__ = ['PerformanceMonitor', 'perf_monitor']
PERF_MON_EOF

echo "✅ Performance monitoring script created"

# 4. Create FastAPI middleware for performance monitoring
echo "🚀 Creating FastAPI performance middleware..."

cat > tools/performance_middleware.py << 'MIDDLEWARE_EOF'
"""
FastAPI middleware for performance monitoring and optimization
"""
import time
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .performance_monitor import perf_monitor

logger = logging.getLogger(__name__)

class PerformanceMiddleware(BaseHTTPMiddleware):
    """Middleware to monitor request performance and add optimizations."""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Add performance headers
        response = await call_next(request)
        
        # Calculate request duration
        duration = time.time() - start_time
        
        # Log request performance
        perf_monitor.log_request(
            endpoint=str(request.url.path),
            duration=duration,
            status_code=response.status_code
        )
        
        # Add performance headers to response
        response.headers["X-Process-Time"] = str(duration)
        response.headers["X-Request-ID"] = str(int(start_time * 1000))
        
        # Add timeout warnings for slow requests
        if duration > 30:
            response.headers["X-Performance-Warning"] = "slow-request"
        elif duration > 10:
            response.headers["X-Performance-Warning"] = "moderate-delay"
        
        return response

# Export
__all__ = ['PerformanceMiddleware']
MIDDLEWARE_EOF

echo "✅ Performance middleware created"

# 5. Update main.py to include performance monitoring
echo "📝 Updating main.py with performance monitoring..."

# Create a backup of main.py
cp main.py main.py.backup

# Add performance monitoring to main.py
cat >> main.py << 'MAIN_UPDATE_EOF'

# Add performance monitoring middleware
from tools.performance_middleware import PerformanceMiddleware

# Add the middleware to the app
app.add_middleware(PerformanceMiddleware)

logger.info("Performance monitoring middleware added to application")
MAIN_UPDATE_EOF

echo "✅ Main.py updated with performance monitoring"

# 6. Create a script to restart services with optimizations
echo "🔄 Creating service restart script..."

cat > restart_optimized.sh << 'RESTART_EOF'
#!/bin/bash

echo "🔄 Restarting TransparentSF with performance optimizations..."

# Stop the service
sudo systemctl stop transparentsf

# Wait for graceful shutdown
sleep 5

# Start the service
sudo systemctl start transparentsf

# Wait for startup
sleep 10

# Check service status
if sudo systemctl is-active --quiet transparentsf; then
    echo "✅ TransparentSF service started successfully"
    
    # Show recent logs
    echo "📋 Recent application logs:"
    sudo journalctl -u transparentsf -n 20 --no-pager
    
    # Show performance metrics
    echo "📊 Checking application health..."
    curl -s http://localhost:8000/backend/health || echo "Health check endpoint not available"
    
else
    echo "❌ TransparentSF service failed to start"
    echo "📋 Error logs:"
    sudo journalctl -u transparentsf -n 50 --no-pager
    exit 1
fi

echo "🎉 TransparentSF restarted with performance optimizations!"
RESTART_EOF

chmod +x restart_optimized.sh

echo "✅ Service restart script created"

# 7. Create a monitoring dashboard script
echo "📊 Creating monitoring dashboard script..."

cat > monitor_performance.sh << 'MONITOR_EOF'
#!/bin/bash

echo "📊 TransparentSF Performance Monitor"
echo "=================================="

# Check service status
echo "🔍 Service Status:"
sudo systemctl status transparentsf --no-pager -l | head -10

echo ""
echo "📈 System Resources:"
echo "CPU Usage: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)%"
echo "Memory Usage: $(free | grep Mem | awk '{printf "%.1f%%", $3/$2 * 100.0}')"
echo "Disk Usage: $(df -h / | awk 'NR==2{printf "%s", $5}')"

echo ""
echo "🌐 Application Health:"
curl -s -w "Response Time: %{time_total}s\nHTTP Status: %{http_code}\n" \
     -o /dev/null http://localhost:8000/backend/health || echo "Health check failed"

echo ""
echo "📋 Recent Performance Logs:"
sudo journalctl -u transparentsf -n 20 --no-pager | grep -E "(SLOW|VERY SLOW|Performance)" || echo "No performance warnings found"

echo ""
echo "🔄 Nginx Status:"
sudo systemctl status nginx --no-pager -l | head -5

echo ""
echo "📊 Database Connections:"
sudo -u postgres psql -c "SELECT count(*) as active_connections FROM pg_stat_activity WHERE state = 'active';" 2>/dev/null || echo "Database connection check failed"

echo ""
echo "⏰ Current Time: $(date)"
echo "🕐 Uptime: $(uptime -p)"
MONITOR_EOF

chmod +x monitor_performance.sh

echo "✅ Performance monitoring dashboard created"

echo ""
echo "🎉 TransparentSF Performance Optimization Complete!"
echo "=================================================="
echo ""
echo "📋 What was optimized:"
echo "   ✅ Database connection settings"
echo "   ✅ Query optimization utilities"
echo "   ✅ Performance monitoring"
echo "   ✅ FastAPI middleware"
echo "   ✅ Service restart script"
echo "   ✅ Monitoring dashboard"
echo ""
echo "🚀 Next steps:"
echo "   1. Run: ./restart_optimized.sh"
echo "   2. Monitor: ./monitor_performance.sh"
echo "   3. Check logs: sudo journalctl -u transparentsf -f"
echo ""
echo "📊 Performance monitoring is now active!"
echo "   • Slow requests (>10s) will be logged"
echo "   • Very slow requests (>30s) will be flagged"
echo "   • System metrics are tracked"
echo ""
