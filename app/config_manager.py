"""配置管理模块"""
import os
import yaml
from typing import Optional
from app.models import AppConfig


# 全局变量
app_config: Optional[AppConfig] = None


def load_config() -> AppConfig:
    """加载配置文件"""
    config_file = os.getenv("CONFIG_FILE", "config/config.yaml")
    
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        return AppConfig(**config_data)
    else:
        # 创建默认配置
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        default_config = {
            "vllm_host": "localhost",
            "vllm_port": 8000,
            "fastapi_host": "0.0.0.0",
            "fastapi_port": 8001,
            "api_keys_file": "config/api_keys.json",
            "rate_limit": {
                "qps": None,
                "concurrent": None,
                "tokens_per_minute": None
            },
            "log_level": "INFO"
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(default_config, f, allow_unicode=True)
        return AppConfig(**default_config)


def init_config() -> AppConfig:
    """初始化配置"""
    global app_config
    app_config = load_config()
    return app_config


def get_config() -> AppConfig:
    """获取配置"""
    if app_config is None:
        return init_config()
    return app_config

