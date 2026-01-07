"""请求限制模块"""
import asyncio
import time
from typing import Dict, Optional
from collections import defaultdict, deque
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, HTTPException
from app.models import RateLimitConfig, APIKeyInfo


class RequestLimiter:
    """请求限制器"""
    
    def __init__(self, config: RateLimitConfig):
        self.config = config
        # QPS限制器（使用slowapi）
        self.limiter = Limiter(key_func=get_remote_address)
        
        # 并发限制器（如果concurrent为None，使用一个很大的值表示不限制）
        concurrent_limit = config.concurrent if config.concurrent is not None else 10000
        self.global_semaphore = asyncio.Semaphore(concurrent_limit)
        self.per_key_semaphores: Dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(concurrent_limit)
        )
        
        # Token限制器（每分钟token数）
        self.token_usage: Dict[str, deque] = defaultdict(lambda: deque())
        self.token_lock = asyncio.Lock()
    
    async def check_concurrent_limit(self, api_key: Optional[str] = None):
        """检查并发限制"""
        # 如果concurrent为None，跳过并发限制检查
        if self.config.concurrent is None:
            return
        
        # 全局并发限制
        try:
            await asyncio.wait_for(
                self.global_semaphore.acquire(),
                timeout=0.001  # 立即检查，不阻塞
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=429,
                detail="达到全局并发连接数限制"
            )
        
        # 每个key的并发限制
        if api_key:
            semaphore = self.per_key_semaphores[api_key]
            try:
                await asyncio.wait_for(
                    semaphore.acquire(),
                    timeout=0.001  # 立即检查，不阻塞
                )
            except asyncio.TimeoutError:
                # 释放全局semaphore
                self.global_semaphore.release()
                raise HTTPException(
                    status_code=429,
                    detail="达到该API Key的并发连接数限制"
                )
    
    async def release_concurrent_limit(self, api_key: Optional[str] = None):
        """释放并发限制"""
        # 如果concurrent为None，跳过释放
        if self.config.concurrent is None:
            return
        
        self.global_semaphore.release()
        if api_key and api_key in self.per_key_semaphores:
            self.per_key_semaphores[api_key].release()
    
    async def check_token_limit(self, api_key: str, tokens: int):
        """检查Token限制"""
        if not self.config.tokens_per_minute:
            return
        
        async with self.token_lock:
            now = time.time()
            # 清理1分钟前的记录
            usage_queue = self.token_usage[api_key]
            while usage_queue and usage_queue[0] < now - 60:
                usage_queue.popleft()
            
            # 计算当前分钟内的token使用量
            current_usage = sum(usage_queue)
            
            if current_usage + tokens > self.config.tokens_per_minute:
                raise HTTPException(
                    status_code=429,
                    detail=f"Token使用量超限（限制: {self.config.tokens_per_minute}/分钟）"
                )
            
            # 记录token使用
            usage_queue.append(tokens)
    
    def get_rate_limit_decorator(self):
        """获取速率限制装饰器"""
        return self.limiter.limit(f"{self.config.qps}/second")


# 全局限制器实例（将在main.py中初始化）
limiter: Optional[RequestLimiter] = None

