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
            config_data = yaml.safe_load(f) or {}
        return AppConfig(**config_data)

    # 创建默认配置
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    default_config = _default_config()
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


def _default_config() -> dict:
    """生成默认配置，用于初始化 config.yaml。"""
    base_config = AppConfig().model_dump()
    # AppConfig().model_dump() 中的 rate_limit 是 BaseModel，需要转为 dict
    return base_config

