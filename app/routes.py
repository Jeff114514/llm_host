"""路由处理模块"""
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key, APIKeyInfo
from app.limiter import RequestLimiter
from app.config_manager import get_config
from app.vllm_client import forward_stream_request as vllm_forward_stream, forward_non_stream_request as vllm_forward_non_stream, forward_get_request as vllm_forward_get
from app.sglang_client import forward_stream_request as sglang_forward_stream, forward_non_stream_request as sglang_forward_non_stream, forward_get_request as sglang_forward_get
from app.model_router import ModelRouter
from app.models import BackendType
from app.monitoring import logger
import httpx


def create_routes(app: FastAPI, request_limiter: RequestLimiter):
    """创建路由"""
    app_config = get_config()
    
    def apply_rate_limit_if_needed(func):
        """如果配置了QPS限制，应用速率限制装饰器"""
        if app_config.rate_limit.qps is not None:
            return request_limiter.limiter.limit(f"{app_config.rate_limit.qps}/second")(func)
        return func

    async def _require_admin(api_key_info: APIKeyInfo):
        if api_key_info.user != "admin":
            logger.warning("admin_permission_denied", user=api_key_info.user)
            raise HTTPException(status_code=401, detail="无权限操作")

    async def _post_to_backend(url: str, body: dict) -> dict:
        """向后端管理端点转发 POST 请求（返回 JSON 或文本）。"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        try:
            return resp.json()
        except Exception:
            return {"message": resp.text}
    
    def _get_model_router(request: Request) -> ModelRouter:
        """从应用状态获取 ModelRouter"""
        router = getattr(request.app.state, "model_router", None)
        if router is None:
            raise HTTPException(status_code=500, detail="模型路由器未初始化")
        return router
    
    def _get_backend_url(router: ModelRouter, backend_type: BackendType, path: str, base_url: Optional[str] = None) -> Optional[str]:
        """获取后端URL"""
        return router.build_url(backend_type, path, base_url)
    
    @app.get("/health")
    async def health_check(request: Request):
        """健康检查端点 - 检查所有后端服务的健康状态
        
        返回状态说明：
        - "healthy": 所有后端服务都正常
        - "degraded": 部分后端服务不可用（至少有一个正常）
        - "unhealthy": 所有后端服务都不可用或没有注册的后端
        
        注意：只检查实际启动的后端服务（通过 manager.is_running() 判断）
        """
        router = getattr(request.app.state, "model_router", None)
        vllm_manager = getattr(request.app.state, "vllm_manager", None)
        sglang_manager = getattr(request.app.state, "sglang_manager", None)
        
        # 如果路由器未初始化，返回不健康状态
        if router is None:
            return {
                "status": "unhealthy",
                "service": "vLLM Proxy",
                "message": "模型路由器未初始化",
                "backends": {}
            }
        
        # 获取所有已注册的后端实例
        all_backends = router.list_backends()
        
        # 获取配置中的默认后端 URL（用于识别通过 manager 启动的默认实例）
        config = get_config()
        default_vllm_url = f"http://{config.vllm_host}:{config.vllm_port}" if config.vllm_host and config.vllm_port else None
        default_sglang_url = f"http://{config.sglang_host}:{config.sglang_port}" if config.sglang_host and config.sglang_port else None
        
        # 过滤出实际启动的后端实例
        # 对于默认后端（通过 manager 启动的），检查 manager.is_running()
        # 对于动态注册的后端，假设它们都是启动的（由管理员负责管理）
        active_backends = []
        
        for backend_info in all_backends:
            base_url = backend_info["base_url"]
            backend_type = backend_info["backend"]
            
            # 如果是默认 vLLM 实例（URL 匹配配置中的默认 URL），检查 vllm_manager 是否运行
            if backend_type == BackendType.VLLM and default_vllm_url and base_url == default_vllm_url:
                if vllm_manager and vllm_manager.is_running():
                    active_backends.append(backend_info)
                # 如果未启动，跳过这个后端
            # 如果是默认 sglang 实例（URL 匹配配置中的默认 URL），检查 sglang_manager 是否运行
            elif backend_type == BackendType.SGLANG and default_sglang_url and base_url == default_sglang_url:
                if sglang_manager and sglang_manager.is_running():
                    active_backends.append(backend_info)
                # 如果未启动，跳过这个后端
            else:
                # 动态注册的后端，假设都是启动的（由管理员负责确保它们运行）
                active_backends.append(backend_info)
        
        if not active_backends:
            # 没有启动的后端
            return {
                "status": "unhealthy",
                "service": "vLLM Proxy",
                "message": "没有启动的后端服务",
                "backends": {}
            }
        
        # 检查每个后端的健康状态
        backend_statuses = {}
        healthy_count = 0
        total_count = len(active_backends)
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            for backend_info in active_backends:
                base_url = backend_info["base_url"]
                backend_type = backend_info["backend"]
                instance_id = backend_info["instance_id"]
                
                health_url = f"{base_url}/health"
                try:
                    resp = await client.get(health_url)
                    if resp.status_code == 200:
                        backend_statuses[instance_id] = {
                            "backend": backend_type,
                            "base_url": base_url,
                            "status": "healthy",
                            "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
                        }
                        healthy_count += 1
                    else:
                        backend_statuses[instance_id] = {
                            "backend": backend_type,
                            "base_url": base_url,
                            "status": "unhealthy",
                            "error": f"HTTP {resp.status_code}"
                        }
                except httpx.TimeoutException:
                    backend_statuses[instance_id] = {
                        "backend": backend_type,
                        "base_url": base_url,
                        "status": "unhealthy",
                        "error": "连接超时"
                    }
                except httpx.ConnectError:
                    backend_statuses[instance_id] = {
                        "backend": backend_type,
                        "base_url": base_url,
                        "status": "unhealthy",
                        "error": "连接失败"
                    }
                except Exception as e:
                    backend_statuses[instance_id] = {
                        "backend": backend_type,
                        "base_url": base_url,
                        "status": "unhealthy",
                        "error": str(e)
                    }
        
        # 根据检查结果确定整体状态
        if healthy_count == 0:
            overall_status = "unhealthy"
        elif healthy_count < total_count:
            overall_status = "degraded"
        else:
            overall_status = "healthy"
        
        # 构建响应
        response = {
            "status": overall_status,
            "service": "vLLM Proxy",
            "backends": backend_statuses,
            "summary": {
                "total": total_count,
                "healthy": healthy_count,
                "unhealthy": total_count - healthy_count
            }
        }
        
        # 为了向后兼容，保留原有的字段（只包含实际启动的后端）
        # 使用配置中的默认 URL，而不是从 router 获取（因为可能已被注销）
        if default_vllm_url and vllm_manager and vllm_manager.is_running():
            response["vllm_url"] = default_vllm_url
        if default_sglang_url and sglang_manager and sglang_manager.is_running():
            response["sglang_url"] = default_sglang_url
        
        return response
    
    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    @apply_rate_limit_if_needed
    async def chat_completions(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """Chat Completions端点 - 根据模型名称自动选择后端（vLLM/sglang）"""
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
            model_name = body.get("model", "")
            
            # 获取模型路由器并选择后端
            router = _get_model_router(request)
            backend_info = router.get_backend_for_model(model_name)
            
            if backend_info is None:
                # 如果模型未找到，尝试刷新模型列表
                logger.info("model_not_found_refreshing", model=model_name)
                await router.refresh_models()
                backend_info = router.get_backend_for_model(model_name)
                
                if backend_info is None:
                    available_models = router.list_models()
                    logger.warning(
                        "model_not_found",
                        model=model_name,
                        available_models=available_models
                    )
                    raise HTTPException(
                        status_code=404,
                        detail=f"模型 '{model_name}' 未找到。可用模型: {', '.join(available_models) if available_models else '无'}"
                    )
            
            backend_type, base_url = backend_info
            
            # 构建后端URL
            backend_url = _get_backend_url(router, backend_type, "/v1/chat/completions", base_url)
            if backend_url is None:
                raise HTTPException(status_code=500, detail=f"无法构建后端 URL，后端类型: {backend_type.value}")
            
            # 估算token数量（简单估算）
            messages = body.get("messages", [])
            estimated_tokens = sum(len(str(msg).split()) for msg in messages) * 2
            
            # 检查token限制
            await request_limiter.check_token_limit(api_key_info.key, estimated_tokens)
            
            # 检查是否是流式请求
            is_stream = body.get("stream", False)
            
            # 记录请求信息用于调试
            logger.debug(
                "forwarding_request_to_backend",
                backend=backend_type.value,
                backend_url=backend_url,
                is_stream=is_stream,
                model=model_name,
                messages_count=len(messages),
                estimated_tokens=estimated_tokens
            )
            
            # 根据后端类型选择对应的客户端函数
            if backend_type == BackendType.VLLM:
                forward_stream = vllm_forward_stream
                forward_non_stream = vllm_forward_non_stream
            else:  # SGLANG
                forward_stream = sglang_forward_stream
                forward_non_stream = sglang_forward_non_stream
            
            if is_stream:
                logger.debug("processing_stream_request")
                result = await forward_stream(backend_url, body, api_key_info.key)
                logger.info("chat_completions_stream_request_completed", user=api_key_info.user, backend=backend_type.value)
                return result
            else:
                logger.debug("processing_non_stream_request")
                result = await forward_non_stream(backend_url, body, api_key_info.key)
                logger.info(
                    "chat_completions_request_completed",
                    user=api_key_info.user,
                    backend=backend_type.value,
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
        """Completions端点 - 根据模型名称自动选择后端（vLLM/sglang）"""
        client_ip = request.client.host if request.client else None
        logger.info(
            "completions_request_started",
            user=api_key_info.user,
            client_ip=client_ip
        )
        
        await request_limiter.check_concurrent_limit(api_key_info.key)
        
        try:
            body = await request.json()
            model_name = body.get("model", "")
            
            # 获取模型路由器并选择后端
            router = _get_model_router(request)
            backend_info = router.get_backend_for_model(model_name)
            
            if backend_info is None:
                # 如果模型未找到，尝试刷新模型列表
                logger.info("model_not_found_refreshing", model=model_name)
                await router.refresh_models()
                backend_info = router.get_backend_for_model(model_name)
                
                if backend_info is None:
                    available_models = router.list_models()
                    logger.warning(
                        "model_not_found",
                        model=model_name,
                        available_models=available_models
                    )
                    raise HTTPException(
                        status_code=404,
                        detail=f"模型 '{model_name}' 未找到。可用模型: {', '.join(available_models) if available_models else '无'}"
                    )
            
            backend_type, base_url = backend_info
            
            # 构建后端URL
            backend_url = _get_backend_url(router, backend_type, "/v1/completions", base_url)
            if backend_url is None:
                raise HTTPException(status_code=500, detail=f"无法构建后端 URL，后端类型: {backend_type.value}")
            
            # 估算token数量
            prompt = body.get("prompt", "")
            estimated_tokens = len(str(prompt).split()) * 2
            
            await request_limiter.check_token_limit(api_key_info.key, estimated_tokens)
            
            logger.debug(
                "forwarding_completions_request",
                backend=backend_type.value,
                backend_url=backend_url,
                estimated_tokens=estimated_tokens
            )
            
            # 根据后端类型选择对应的客户端函数
            if backend_type == BackendType.VLLM:
                forward_non_stream = vllm_forward_non_stream
            else:  # SGLANG
                forward_non_stream = sglang_forward_non_stream
            
            result = await forward_non_stream(backend_url, body, api_key_info.key)
            
            logger.info(
                "completions_request_completed",
                user=api_key_info.user,
                backend=backend_type.value,
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
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key)
    ):
        """列出所有可用模型（聚合 vLLM 和 sglang 后端的模型列表）"""
        logger.debug("list_models_requested", user=api_key_info.user)
        
        # 使用 ModelRouter 聚合模型列表
        router = _get_model_router(request)
        result = router.list_models_openai_payload()
        
        logger.debug(
            "list_models_completed",
            user=api_key_info.user,
            model_count=len(result.get("data", []))
        )
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

    @app.post("/admin/register-backend")
    async def register_backend(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """动态注册后端实例（需要管理员权限）
        
        请求体示例：
        {
            "backend": "vllm",  // 或 "sglang"
            "base_url": "http://localhost:8004"
        }
        """
        await _require_admin(api_key_info)
        router = _get_model_router(request)
        body = await request.json()
        
        backend_str = body.get("backend", "").lower()
        base_url = body.get("base_url", "").strip()
        
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url 不能为空")
        
        if backend_str not in ["vllm", "sglang"]:
            raise HTTPException(status_code=400, detail="backend 必须是 'vllm' 或 'sglang'")
        
        backend_type = BackendType.VLLM if backend_str == "vllm" else BackendType.SGLANG
        
        logger.info("admin_register_backend_requested", user=api_key_info.user, backend=backend_str, base_url=base_url)
        
        try:
            instance_id = router.register_backend(backend_type, base_url)
            # 注册后自动刷新模型列表
            await router.refresh_models()
            
            logger.info("admin_register_backend_completed", user=api_key_info.user, instance_id=instance_id)
            
            return {
                "message": "后端实例已注册",
                "instance_id": instance_id,
                "backend": backend_str,
                "base_url": base_url
            }
        except Exception as e:
            logger.error("admin_register_backend_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"注册后端失败: {str(e)}")
    
    @app.post("/admin/unregister-backend")
    async def unregister_backend(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """动态注销后端实例（需要管理员权限）
        
        请求体示例：
        {
            "base_url": "http://localhost:8004"
        }
        """
        await _require_admin(api_key_info)
        router = _get_model_router(request)
        body = await request.json()
        
        base_url = body.get("base_url", "").strip()
        
        if not base_url:
            raise HTTPException(status_code=400, detail="base_url 不能为空")
        
        logger.info("admin_unregister_backend_requested", user=api_key_info.user, base_url=base_url)
        
        try:
            success = router.unregister_backend(base_url)
            if success:
                # 注销后刷新模型列表
                await router.refresh_models()
                logger.info("admin_unregister_backend_completed", user=api_key_info.user, base_url=base_url)
                return {
                    "message": "后端实例已注销",
                    "base_url": base_url
                }
            else:
                raise HTTPException(status_code=404, detail=f"后端实例未找到: {base_url}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("admin_unregister_backend_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"注销后端失败: {str(e)}")
    
    @app.get("/admin/list-backends")
    async def list_backends(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """列出所有已注册的后端实例（需要管理员权限）"""
        await _require_admin(api_key_info)
        router = _get_model_router(request)
        
        backends = router.list_backends()
        
        return {
            "backends": backends,
            "count": len(backends)
        }
    
    @app.post("/admin/refresh-models")
    async def refresh_models(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """刷新模型列表（从所有后端重新发现模型，需要管理员权限）"""
        await _require_admin(api_key_info)
        router = _get_model_router(request)
        
        logger.info("admin_refresh_models_requested", user=api_key_info.user)
        discovered = await router.refresh_models()
        
        all_models = router.list_models()
        logger.info(
            "admin_refresh_models_completed",
            user=api_key_info.user,
            discovered_count=len(discovered),
            total_models=len(all_models)
        )
        
        return {
            "message": "模型列表已刷新",
            "discovered_models": {k: {"backend": v[0].value, "base_url": v[1]} for k, v in discovered.items()},
            "total_models": all_models,
            "model_count": len(all_models)
        }
    
    @app.post("/admin/start-vllm")
    async def start_vllm(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """启动 vLLM 后端服务（需要管理员权限）"""
        await _require_admin(api_key_info)
        vllm_manager = getattr(request.app.state, "vllm_manager", None)
        
        if vllm_manager is None:
            raise HTTPException(status_code=500, detail="vLLM 管理器未初始化")
        
        if vllm_manager.is_running():
            # 通过内部方法获取 PID（如果可用）
            pid = getattr(vllm_manager, '_read_pid', lambda: None)()
            return {
                "message": "vLLM 服务已在运行",
                "pid": pid,
                "status": "running"
            }
        
        logger.info("admin_start_vllm_requested", user=api_key_info.user)
        try:
            pid = vllm_manager.start()
            ready = vllm_manager.wait_for_ready(
                app_config.vllm_host,
                app_config.vllm_port,
                timeout=60,
            )
            
            # 启动后刷新模型列表
            router = _get_model_router(request)
            await router.refresh_models()
            
            logger.info(
                "admin_start_vllm_completed",
                user=api_key_info.user,
                pid=pid,
                ready=ready
            )
            
            return {
                "message": "vLLM 服务已启动",
                "pid": pid,
                "ready": ready,
                "status": "started"
            }
        except Exception as e:
            logger.error("admin_start_vllm_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"启动 vLLM 失败: {str(e)}")
    
    @app.post("/admin/stop-vllm")
    async def stop_vllm(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """停止 vLLM 后端服务（需要管理员权限）"""
        await _require_admin(api_key_info)
        vllm_manager = getattr(request.app.state, "vllm_manager", None)
        
        if vllm_manager is None:
            raise HTTPException(status_code=500, detail="vLLM 管理器未初始化")
        
        if not vllm_manager.is_running():
            return {
                "message": "vLLM 服务未运行",
                "status": "stopped"
            }
        
        logger.info("admin_stop_vllm_requested", user=api_key_info.user)
        try:
            vllm_manager.stop()
            
            # 停止后刷新模型列表
            router = _get_model_router(request)
            await router.refresh_models()
            
            logger.info("admin_stop_vllm_completed", user=api_key_info.user)
            
            return {
                "message": "vLLM 服务已停止",
                "status": "stopped"
            }
        except Exception as e:
            logger.error("admin_stop_vllm_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"停止 vLLM 失败: {str(e)}")
    
    @app.post("/admin/start-sglang")
    async def start_sglang(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """启动 sglang 后端服务（需要管理员权限）"""
        await _require_admin(api_key_info)
        sglang_manager = getattr(request.app.state, "sglang_manager", None)
        
        if sglang_manager is None:
            raise HTTPException(status_code=500, detail="sglang 管理器未初始化")
        
        if sglang_manager.is_running():
            # 通过内部方法获取 PID（如果可用）
            pid = getattr(sglang_manager, '_read_pid', lambda: None)()
            return {
                "message": "sglang 服务已在运行",
                "pid": pid,
                "status": "running"
            }
        
        logger.info("admin_start_sglang_requested", user=api_key_info.user)
        try:
            pid = sglang_manager.start()
            ready = sglang_manager.wait_for_ready(
                app_config.sglang_host,
                app_config.sglang_port,
                timeout=60,
            )
            
            # 启动后刷新模型列表
            router = _get_model_router(request)
            await router.refresh_models()
            
            logger.info(
                "admin_start_sglang_completed",
                user=api_key_info.user,
                pid=pid,
                ready=ready
            )
            
            return {
                "message": "sglang 服务已启动",
                "pid": pid,
                "ready": ready,
                "status": "started"
            }
        except Exception as e:
            logger.error("admin_start_sglang_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"启动 sglang 失败: {str(e)}")
    
    @app.post("/admin/stop-sglang")
    async def stop_sglang(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """停止 sglang 后端服务（需要管理员权限）"""
        await _require_admin(api_key_info)
        sglang_manager = getattr(request.app.state, "sglang_manager", None)
        
        if sglang_manager is None:
            raise HTTPException(status_code=500, detail="sglang 管理器未初始化")
        
        if not sglang_manager.is_running():
            return {
                "message": "sglang 服务未运行",
                "status": "stopped"
            }
        
        logger.info("admin_stop_sglang_requested", user=api_key_info.user)
        try:
            sglang_manager.stop()
            
            # 停止后刷新模型列表
            router = _get_model_router(request)
            await router.refresh_models()
            
            logger.info("admin_stop_sglang_completed", user=api_key_info.user)
            
            return {
                "message": "sglang 服务已停止",
                "status": "stopped"
            }
        except Exception as e:
            logger.error("admin_stop_sglang_failed", user=api_key_info.user, error=str(e))
            raise HTTPException(status_code=500, detail=f"停止 sglang 失败: {str(e)}")
    
    @app.get("/admin/backend-status")
    async def get_backend_status(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """获取后端服务状态（需要管理员权限）"""
        await _require_admin(api_key_info)
        
        vllm_manager = getattr(request.app.state, "vllm_manager", None)
        sglang_manager = getattr(request.app.state, "sglang_manager", None)
        router = _get_model_router(request)
        
        # 获取默认后端状态（通过管理器启动的）
        default_vllm_running = vllm_manager.is_running() if vllm_manager else False
        default_sglang_running = sglang_manager.is_running() if sglang_manager else False
        
        # 获取所有已注册的后端实例
        registered_backends = router.list_backends() if router else []
        
        status = {
            "default_vllm": {
                "running": default_vllm_running,
                "pid": getattr(vllm_manager, '_read_pid', lambda: None)() if vllm_manager and default_vllm_running else None,
                "url": router.get_base_url(BackendType.VLLM) if router else None
            },
            "default_sglang": {
                "running": default_sglang_running,
                "pid": getattr(sglang_manager, '_read_pid', lambda: None)() if sglang_manager and default_sglang_running else None,
                "url": router.get_base_url(BackendType.SGLANG) if router else None
            },
            "registered_backends": registered_backends,
            "models": router.list_models() if router else []
        }
        
        return status
    
    @app.post("/admin/load-lora-adapter")
    async def load_lora_adapter(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """动态加载 LoRA（需要管理员权限）
        
        请求体可以包含可选的 base_url 字段来指定 vLLM 实例：
        {
            "lora_name": "sql_adapter",
            "lora_path": "/data/lora/sql",
            "base_url": "http://localhost:8004"  // 可选，不指定则使用默认 vLLM 实例
        }
        """
        await _require_admin(api_key_info)
        body = await request.json()
        router = _get_model_router(request)
        
        # 如果指定了 base_url，使用指定的实例；否则使用默认的 vLLM 实例
        base_url = body.pop("base_url", None)
        vllm_url = router.build_url(BackendType.VLLM, "/v1/load_lora_adapter", base_url)
        
        if vllm_url is None:
            raise HTTPException(status_code=404, detail="未找到可用的 vLLM 实例")
        
        logger.info("admin_load_lora_requested", user=api_key_info.user, payload=body, base_url=base_url)
        result = await _post_to_backend(vllm_url, body)
        logger.info("admin_load_lora_completed", user=api_key_info.user, result=result)
        return result

    @app.post("/admin/unload-lora-adapter")
    async def unload_lora_adapter(
        request: Request,
        api_key_info: APIKeyInfo = Depends(verify_api_key),
    ):
        """动态卸载 LoRA（需要管理员权限）
        
        请求体可以包含可选的 base_url 字段来指定 vLLM 实例：
        {
            "lora_name": "sql_adapter",
            "base_url": "http://localhost:8004"  // 可选，不指定则使用默认 vLLM 实例
        }
        """
        await _require_admin(api_key_info)
        body = await request.json()
        router = _get_model_router(request)
        
        # 如果指定了 base_url，使用指定的实例；否则使用默认的 vLLM 实例
        base_url = body.pop("base_url", None)
        vllm_url = router.build_url(BackendType.VLLM, "/v1/unload_lora_adapter", base_url)
        
        if vllm_url is None:
            raise HTTPException(status_code=404, detail="未找到可用的 vLLM 实例")
        
        logger.info("admin_unload_lora_requested", user=api_key_info.user, payload=body, base_url=base_url)
        result = await _post_to_backend(vllm_url, body)
        logger.info("admin_unload_lora_completed", user=api_key_info.user, result=result)
        return result
    
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

