"""数据模型定义"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class APIKeyInfo(BaseModel):
    """API Key信息模型"""
    key: str
    user: Optional[str] = None
    quota: Optional[int] = None
    enabled: bool = True


class RateLimitConfig(BaseModel):
    """速率限制配置"""
    qps: Optional[int] = None  # 每秒请求数
    concurrent: Optional[int] = None  # 并发连接数
    tokens_per_minute: Optional[int] = None  # 每分钟token数


class AppConfig(BaseModel):
    """应用配置模型"""
    vllm_host: str = "localhost"
    vllm_port: int = 8002
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8001
    api_keys_file: str = "config/api_keys.json"
    rate_limit: RateLimitConfig = RateLimitConfig()
    log_level: str = "INFO"

