"""路由处理模块"""
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key, APIKeyInfo
from app.limiter import RequestLimiter
from app.config_manager import get_config
from app.vllm_client import forward_stream_request, forward_non_stream_request, forward_get_request
from app.monitoring import logger


def create_routes(app: FastAPI, request_limiter: RequestLimiter):
    """创建路由"""
    app_config = get_config()
    
    def apply_rate_limit_if_needed(func):
        """如果配置了QPS限制，应用速率限制装饰器"""
        if app_config.rate_limit.qps is not None:
            return request_limiter.limiter.limit(f"{app_config.rate_limit.qps}/second")(func)
        return func
    
    @app.get("/health")
    async def health_check():
        """健康检查端点"""
        return {
            "status": "healthy",
            "service": "vLLM Proxy",
            "vllm_url": f"http://{app_config.vllm_host}:{app_config.vllm_port}"
        }
    
    @app.get("/metrics")
    async def metrics():
        """Prometheus指标端点（由instrumentator自动提供）"""
        pass
    
    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    @apply_rate_limit_if_needed
    async def chat_completions(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """Chat Completions端点"""
        client_ip = request.client.host if request.client else None
        
        # 记录请求开始
        logger.info(
            "chat_completions_request_started",
            user=api_key_info.user,
            client_ip=client_ip
        )
        
        # 检查并发限制
        await request_limiter.check_concurrent_limit(api_key_info.key)
        
        try:
            # 获取请求体
            body = await request.json()
            
            # 估算token数量（简单估算）
            messages = body.get("messages", [])
            estimated_tokens = sum(len(str(msg).split()) for msg in messages) * 2
            
            # 检查token限制
            await request_limiter.check_token_limit(api_key_info.key, estimated_tokens)
            
            # 转发请求到vLLM
            vllm_url = f"http://{app_config.vllm_host}:{app_config.vllm_port}/v1/chat/completions"
            
            # 检查是否是流式请求
            is_stream = body.get("stream", False)
            
            # 记录请求信息用于调试
            logger.debug(
                "forwarding_request_to_vllm",
                vllm_url=vllm_url,
                is_stream=is_stream,
                model=body.get("model"),
                messages_count=len(body.get("messages", [])),
                estimated_tokens=estimated_tokens
            )
            
            if is_stream:
                logger.debug("processing_stream_request")
                result = await forward_stream_request(vllm_url, body, api_key_info.key)
                logger.info("chat_completions_stream_request_completed", user=api_key_info.user)
                return result
            else:
                logger.debug("processing_non_stream_request")
                result = await forward_non_stream_request(vllm_url, body, api_key_info.key)
                logger.info(
                    "chat_completions_request_completed",
                    user=api_key_info.user,
                    has_usage="usage" in result if isinstance(result, dict) else False
                )
                return result
            
        except HTTPException as e:
            logger.warning(
                "chat_completions_http_error",
                status_code=e.status_code,
                user=api_key_info.user,
                client_ip=client_ip
            )
            raise
        except Exception as e:
            logger.error(
                "chat_completions_error",
                error=str(e),
                error_type=type(e).__name__,
                user=api_key_info.user,
                client_ip=client_ip
            )
            raise HTTPException(
                status_code=500,
                detail=f"内部服务器错误: {str(e)}"
            )
        finally:
            # 释放并发限制
            await request_limiter.release_concurrent_limit(api_key_info.key)
    
    @app.post("/v1/completions")
    @app.post("/completions")
    @apply_rate_limit_if_needed
    async def completions(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """Completions端点"""
        client_ip = request.client.host if request.client else None
        logger.info(
            "completions_request_started",
            user=api_key_info.user,
            client_ip=client_ip
        )
        
        await request_limiter.check_concurrent_limit(api_key_info.key)
        
        try:
            body = await request.json()
            
            # 估算token数量
            prompt = body.get("prompt", "")
            estimated_tokens = len(str(prompt).split()) * 2
            
            await request_limiter.check_token_limit(api_key_info.key, estimated_tokens)
            
            vllm_url = f"http://{app_config.vllm_host}:{app_config.vllm_port}/v1/completions"
            
            logger.debug(
                "forwarding_completions_request",
                vllm_url=vllm_url,
                estimated_tokens=estimated_tokens
            )
            
            result = await forward_non_stream_request(vllm_url, body, api_key_info.key)
            
            logger.info(
                "completions_request_completed",
                user=api_key_info.user,
                has_usage="usage" in result if isinstance(result, dict) else False
            )
            
            return result
            
        except HTTPException as e:
            logger.warning(
                "completions_http_error",
                status_code=e.status_code,
                user=api_key_info.user
            )
            raise
        except Exception as e:
            logger.error(
                "completions_error",
                error=str(e),
                error_type=type(e).__name__,
                user=api_key_info.user,
                client_ip=client_ip
            )
            raise HTTPException(
                status_code=500,
                detail=f"内部服务器错误: {str(e)}"
            )
        finally:
            await request_limiter.release_concurrent_limit(api_key_info.key)
    
    @app.get("/v1/models")
    @app.get("/models")
    async def list_models(
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """列出可用模型"""
        logger.debug("list_models_requested", user=api_key_info.user)
        vllm_url = f"http://{app_config.vllm_host}:{app_config.vllm_port}/v1/models"
        result = await forward_get_request(vllm_url)
        logger.debug("list_models_completed", user=api_key_info.user)
        return result
    
    @app.post("/admin/reload-keys")
    async def reload_api_keys(
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """重新加载API keys（需要管理员权限）"""
        from app.auth import APIKeyAuth
        
        logger.info("admin_reload_keys_requested", user=api_key_info.user)
        
        # 检查管理员权限
        if api_key_info.user != "admin":
            logger.warning("admin_reload_keys_unauthorized", user=api_key_info.user)
            raise HTTPException(
                status_code=401,
                detail="无权限操作"
            )
        
        # 重新加载API keys
        config = get_config()
        auth_manager = APIKeyAuth(config.api_keys_file)
        auth_manager.reload_keys()
        
        logger.info("admin_reload_keys_completed", user=api_key_info.user)
        return {"message": "API keys已重新加载"}
    
    @app.post("/admin/clean-logs")
    async def clean_logs(
        api_key_info: APIKeyInfo = Depends(verify_api_key),
        days: int = 7
    ):
        """清理日志文件（需要管理员权限）"""
        import os
        from app.log_manager import clean_old_logs, get_log_stats
        
        logger.info("admin_clean_logs_requested", user=api_key_info.user, days=days)
        
        # 检查管理员权限
        if api_key_info.user != "admin":
            logger.warning("admin_clean_logs_unauthorized", user=api_key_info.user)
            raise HTTPException(
                status_code=401,
                detail="无权限操作"
            )
        
        # 清理日志
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        result = clean_old_logs(log_dir, days_to_keep=days)
        
        # 获取清理后的统计信息
        stats = get_log_stats(log_dir)
        
        logger.info(
            "admin_clean_logs_completed",
            user=api_key_info.user,
            deleted_files=result["deleted_files"],
            freed_space_mb=result["freed_space_mb"]
        )
        
        return {
            "message": "日志清理完成",
            "cleanup_result": result,
            "current_stats": stats
        }
    
    @app.get("/admin/log-stats")
    async def get_log_statistics(
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """获取日志统计信息（需要管理员权限）"""
        import os
        from app.log_manager import get_log_stats
        
        logger.debug("admin_log_stats_requested", user=api_key_info.user)
        
        # 检查管理员权限
        if api_key_info.user != "admin":
            logger.warning("admin_log_stats_unauthorized", user=api_key_info.user)
            raise HTTPException(
                status_code=401,
                detail="无权限操作"
            )
        
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        stats = get_log_stats(log_dir)
        return stats

