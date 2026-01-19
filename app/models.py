"""数据模型定义"""
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


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


class LoRAPreloadModule(BaseModel):
    """预加载的LoRA模块"""
    name: str
    path: str
    base_model_name: Optional[str] = None


class LoRARuntimeResolver(BaseModel):
    """LoRA运行时解析配置"""
    allow_runtime_updates: bool = True
    plugins: List[str] = Field(
        default_factory=lambda: ["lora_filesystem_resolver"]
    )
    cache_dir: Optional[str] = "./lora_cache"


class LoRASettings(BaseModel):
    """LoRA 总体配置"""
    enabled: bool = True
    max_lora_rank: int = 64
    max_loras: int = 4
    max_cpu_loras: int = 2
    preload: List[LoRAPreloadModule] = Field(default_factory=list)
    default_mm_loras: Dict[str, str] = Field(default_factory=dict)
    limit_mm_per_prompt: Dict[str, int] = Field(default_factory=dict)
    runtime_resolver: LoRARuntimeResolver = Field(
        default_factory=LoRARuntimeResolver
    )


class PythonLauncherConfig(BaseModel):
    """Python 启动器配置"""
    enabled: bool = True
    conda_env: Optional[str] = None
    env_file: Optional[str] = None


class VLLMLaunchMode(str, Enum):
    """vLLM 启动模式"""
    PYTHON_API = "python_api"
    CLI = "cli"


class VLLMConfig(BaseModel):
    """vLLM 启动及运行配置"""
    auto_start: bool = False
    launch_mode: VLLMLaunchMode = VLLMLaunchMode.PYTHON_API
    start_cmd_file: str = "config/vllm_start_cmd.txt"
    start_cmd: Optional[str] = None
    log_dir: str = "logs"
    log_file: str = "logs/vllm.log"
    log_max_size_mb: float = 100.0
    pid_dir: str = ".pids"
    pid_file: str = ".pids/vllm.pid"
    python_launcher: PythonLauncherConfig = Field(
        default_factory=PythonLauncherConfig
    )
    extra_env: Dict[str, str] = Field(default_factory=dict)
    lora: LoRASettings = Field(default_factory=LoRASettings)


class AppConfig(BaseModel):
    """应用配置模型"""
    vllm_host: str = "localhost"
    vllm_port: int = 8002
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8001
    api_keys_file: str = "config/api_keys.json"
    rate_limit: RateLimitConfig = RateLimitConfig()
    log_level: str = "INFO"
    vllm: VLLMConfig = Field(default_factory=VLLMConfig)

