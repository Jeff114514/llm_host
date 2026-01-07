"""API Key认证模块"""
import json
import os
from typing import Optional, Dict
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.models import APIKeyInfo


class APIKeyAuth:
    """API Key认证管理器"""
    
    def __init__(self, api_keys_file: str):
        self.api_keys_file = api_keys_file
        self.api_keys: Dict[str, APIKeyInfo] = {}
        self.load_api_keys()
    
    def load_api_keys(self):
        """从文件加载API keys"""
        if not os.path.exists(self.api_keys_file):
            # 创建默认的API keys文件
            os.makedirs(os.path.dirname(self.api_keys_file), exist_ok=True)
            default_keys = {
                "keys": [
                    {
                        "key": "sk-default-key-change-me",
                        "user": "default",
                        "quota": 10000,
                        "enabled": True
                    }
                ]
            }
            with open(self.api_keys_file, 'w', encoding='utf-8') as f:
                json.dump(default_keys, f, indent=2, ensure_ascii=False)
        
        with open(self.api_keys_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.api_keys = {}
        for key_info in data.get('keys', []):
            key_data = APIKeyInfo(**key_info)
            self.api_keys[key_data.key] = key_data
    
    def verify_key(self, api_key: str) -> Optional[APIKeyInfo]:
        """验证API key"""
        if api_key not in self.api_keys:
            return None
        
        key_info = self.api_keys[api_key]
        if not key_info.enabled:
            return None
        
        return key_info
    
    def reload_keys(self):
        """重新加载API keys（支持热重载）"""
        self.load_api_keys()


# API Key Header
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def get_auth_manager():
    """获取认证管理器实例（延迟导入避免循环依赖）"""
    from app.main import auth_manager
    return auth_manager


async def verify_api_key(
    authorization: Optional[str] = Security(api_key_header)
) -> APIKeyInfo:
    """
    验证API Key中间件
    
    支持格式：
    - Authorization: Bearer sk-xxx
    - Authorization: sk-xxx
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="缺少API Key，请在请求头中添加: Authorization: Bearer sk-xxx"
        )
    
    # 移除Bearer前缀（如果存在）
    api_key = authorization.replace("Bearer ", "").strip()
    
    # 从全局获取auth实例（将在main.py中初始化）
    auth_manager = get_auth_manager()
    if not auth_manager:
        raise HTTPException(
            status_code=500,
            detail="认证管理器未初始化"
        )
    
    key_info = auth_manager.verify_key(api_key)
    if not key_info:
        raise HTTPException(
            status_code=401,
            detail="无效的API Key"
        )
    
    return key_info

