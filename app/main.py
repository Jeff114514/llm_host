"""FastAPI主应用"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.config_manager import init_config, get_config
from app.auth import APIKeyAuth
from app.limiter import RequestLimiter
from app.monitoring import MonitoringMiddleware, logger
from app.routes import create_routes
from app.vllm_manager import VLLMManager

# 初始化配置和应用组件
app_config = init_config()
auth_manager = APIKeyAuth(app_config.api_keys_file)
request_limiter = RequestLimiter(app_config.rate_limit)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    import asyncio
    import os
    from app.log_manager import setup_log_rotation, clean_old_logs, rotate_log_file
    
    config = get_config()
    vllm_manager = VLLMManager(config)
    app.state.vllm_manager = vllm_manager
    vllm_started = False
    # 启动时执行
    logger.info(
        "application_started",
        vllm_url=f"http://{config.vllm_host}:{config.vllm_port}",
        fastapi_port=config.fastapi_port
    )

    if config.vllm.auto_start and not vllm_manager.is_running():
        try:
            pid = vllm_manager.start()
            ready = vllm_manager.wait_for_ready(
                config.vllm_host,
                config.vllm_port,
                timeout=60,
            )
            vllm_started = True
            logger.info(
                "vllm_auto_start_success",
                pid=pid,
                ready=ready,
                host=config.vllm_host,
                port=config.vllm_port,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("vllm_auto_start_failed", error=str(exc))
            raise
    
    # 启动时清理旧日志（保留7天）
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    try:
        cleanup_result = clean_old_logs(log_dir, days_to_keep=7)
        if cleanup_result["deleted_files"] > 0:
            logger.info(
                "startup_log_cleanup",
                deleted_files=cleanup_result["deleted_files"],
                freed_space_mb=cleanup_result["freed_space_mb"]
            )
        
        # 检查并轮转大文件
        for log_file in [os.path.join(log_dir, "fastapi.log"), os.path.join(log_dir, "vllm.log")]:
            rotate_log_file(log_file, max_size_mb=100.0)
    except Exception as e:
        logger.warning("startup_log_cleanup_failed", error=str(e))
    
    # 启动后台日志轮转任务（每24小时检查一次）
    rotation_task = None
    try:
        rotation_task = asyncio.create_task(
            setup_log_rotation(log_dir, max_size_mb=100.0, days_to_keep=7, check_interval_hours=24)()
        )
    except Exception as e:
        logger.warning("log_rotation_task_failed", error=str(e))
    
    yield
    
    # 关闭时执行
    if rotation_task:
        rotation_task.cancel()
        try:
            await rotation_task
        except asyncio.CancelledError:
            pass
    if vllm_started:
        vllm_manager.stop()


# 创建FastAPI应用
app = FastAPI(
    title="vLLM Proxy API",
    description="FastAPI代理服务，提供API key认证、请求限制和监控",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加监控中间件
app.add_middleware(MonitoringMiddleware)

# 配置Prometheus指标
Instrumentator().instrument(app).expose(app)

# 配置速率限制异常处理
app.state.limiter = request_limiter.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 创建路由
create_routes(app, request_limiter)

if __name__ == "__main__":
    import uvicorn
    config = get_config()
    uvicorn.run(
        "app.main:app",
        host=config.fastapi_host,
        port=config.fastapi_port,
        log_level=config.log_level.lower()
    )

