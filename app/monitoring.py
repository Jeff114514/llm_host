"""监控模块"""
import time
import logging
import structlog
from typing import Dict, Optional
from prometheus_client import Counter, Histogram, Gauge
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


# Prometheus指标
request_count = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)

request_duration = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint']
)

active_requests = Gauge(
    'http_active_requests',
    'Active HTTP requests'
)

token_usage_total = Counter(
    'token_usage_total',
    'Total tokens used',
    ['api_key', 'type']  # type: input/output
)

error_count = Counter(
    'http_errors_total',
    'Total HTTP errors',
    ['method', 'endpoint', 'error_type']
)


# 配置 structlog 使用标准 logging 处理器
# 这样日志会正确输出到 stdout/stderr，可以被启动脚本重定向到文件
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()  # JSON格式输出
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# 结构化日志
logger = structlog.get_logger()


class MonitoringMiddleware(BaseHTTPMiddleware):
    """监控中间件"""
    
    async def dispatch(self, request: Request, call_next):
        """处理请求并记录指标"""
        start_time = time.time()
        active_requests.inc()
        
        method = request.method
        endpoint = request.url.path
        
        try:
            response = await call_next(request)
            status_code = response.status_code
            
            # 记录请求指标
            request_count.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code
            ).inc()
            
            # 记录响应时间
            duration = time.time() - start_time
            request_duration.labels(
                method=method,
                endpoint=endpoint
            ).observe(duration)
            
            # 记录日志
            logger.info(
                "request_completed",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration=duration,
                client_ip=request.client.host if request.client else None
            )
            
            # 记录错误
            if status_code >= 400:
                error_count.labels(
                    method=method,
                    endpoint=endpoint,
                    error_type=f"http_{status_code}"
                ).inc()
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            error_count.labels(
                method=method,
                endpoint=endpoint,
                error_type=type(e).__name__
            ).inc()
            
            logger.error(
                "request_failed",
                method=method,
                endpoint=endpoint,
                error=str(e),
                duration=duration,
                client_ip=request.client.host if request.client else None
            )
            raise
        
        finally:
            active_requests.dec()


def record_token_usage(api_key: str, input_tokens: int, output_tokens: int):
    """记录Token使用量"""
    token_usage_total.labels(api_key=api_key[:8] + "...", type="input").inc(input_tokens)
    token_usage_total.labels(api_key=api_key[:8] + "...", type="output").inc(output_tokens)
    
    logger.info(
        "token_usage",
        api_key=api_key[:8] + "...",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens
    )

