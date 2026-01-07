"""vLLM客户端模块 - 处理与vLLM服务的通信"""
import json
import asyncio
import httpx
from typing import AsyncIterator, Dict, Any, Optional
from fastapi.responses import StreamingResponse
from fastapi import HTTPException

from app.monitoring import record_token_usage, logger
from app.config_manager import get_config

# 全局HTTP客户端连接池（支持高并发）
_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()


def _get_http_client() -> httpx.AsyncClient:
    """获取全局HTTP客户端（单例模式，支持连接池复用）"""
    global _http_client
    if _http_client is None:
        # 配置连接池以支持高并发（512+）
        # 注意：每个worker进程都有独立的httpx客户端实例
        # 对于512并发，考虑流式响应连接占用时间长，需要更大的连接池
        # 每个worker需要支持至少128个并发（512/4），但流式响应时连接会保持较长时间
        # 因此设置更大的keepalive连接池，确保有足够缓冲
        limits = httpx.Limits(
            max_keepalive_connections=1024,  # 保持的连接数（增加以支持512+并发和流式响应）
            max_connections=2048,  # 最大连接数（增加以支持突发连接）
            keepalive_expiry=600.0  # keepalive超时时间（秒，增加以支持长流式响应）
        )
        _http_client = httpx.AsyncClient(
            limits=limits,
            follow_redirects=True,
            timeout=httpx.Timeout(300.0, connect=30.0)  # 默认超时配置
        )
        logger.info(
            "http_client_initialized",
            max_keepalive_connections=limits.max_keepalive_connections,
            max_connections=limits.max_connections,
            keepalive_expiry=limits.keepalive_expiry
        )
    return _http_client


async def close_http_client():
    """关闭全局HTTP客户端（应用关闭时调用）"""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.info("http_client_closed")


async def process_stream_monitoring(collected_data: list, api_key: str, request_body: dict):
    """异步处理流式响应的监控数据（不阻塞响应流）"""
    try:
        if not collected_data:
            return
        
        # 尝试解析收集的数据以提取token使用量
        try:
            # 合并收集的数据
            text_data = b''.join(collected_data).decode('utf-8', errors='ignore')
            
            # 查找最后一个完整的JSON对象（通常是usage信息）
            lines = text_data.split('\n')
            for line in reversed(lines):
                if line.startswith('data: ') and line.strip() != 'data: [DONE]':
                    try:
                        data_str = line[6:].strip()
                        if data_str:
                            data = json.loads(data_str)
                            if 'usage' in data:
                                usage = data['usage']
                                input_tokens = usage.get('prompt_tokens', 0)
                                output_tokens = usage.get('completion_tokens', 0)
                                if input_tokens > 0 or output_tokens > 0:
                                    record_token_usage(api_key, input_tokens, output_tokens)
                                    break
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.debug("stream_monitoring_error", error=str(e))
    except Exception as e:
        logger.error("process_stream_monitoring_error", error=str(e))


def _get_timeout_config(is_stream: bool) -> httpx.Timeout:
    """获取超时配置"""
    if is_stream:
        # 流式响应：不设置读取超时，允许长时间流式传输
        return httpx.Timeout(
            connect=30.0,
            read=None,  # 流式响应不设置读取超时
            write=30.0,
            pool=30.0
        )
    else:
        # 非流式响应：设置正常的超时
        return httpx.Timeout(300.0)


async def forward_stream_request(
    vllm_url: str,
    body: Dict[str, Any],
    api_key: str
) -> StreamingResponse:
    """转发流式请求到vLLM"""
    logger.info(
        "forwarding_stream_request",
        vllm_url=vllm_url,
        model=body.get("model"),
        api_key=api_key[:8] + "..." if api_key else None
    )
    
    timeout_config = _get_timeout_config(is_stream=True)
    collected_data = []
    stream_finished = False
    chunk_count = 0
    total_bytes = 0
    
    async def generate():
        nonlocal collected_data, stream_finished, chunk_count, total_bytes
        # 使用全局HTTP客户端（连接池复用）
        client = _get_http_client()
        # 使用stream方法，连接会在流结束后自动返回到连接池
        async with client.stream(
            "POST",
            vllm_url,
            json=body,
            headers={
                "Content-Type": "application/json"
            },
            timeout=timeout_config
        ) as response:
                # 记录响应状态和头部信息
                logger.debug(
                    "vllm_stream_response_started",
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content_type=response.headers.get("content-type", "unknown")
                )
                
                # 检查状态码
                if response.status_code != 200:
                    try:
                        error_text = await response.aread()
                        error_detail = error_text.decode('utf-8', errors='ignore')
                    except Exception as e:
                        error_detail = f"vLLM服务返回错误状态码: {response.status_code}, 错误: {str(e)}"
                    logger.error(
                        "vllm_stream_error",
                        status_code=response.status_code,
                        error=error_detail
                    )
                    error_msg = f"data: {{\"error\": \"{error_detail}\"}}\n\n".encode('utf-8')
                    yield error_msg
                    return
                
                try:
                    # 直接透传字节流，不做任何处理
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            chunk_count += 1
                            total_bytes += len(chunk)
                            # 记录前几个chunk用于调试
                            if chunk_count <= 3:
                                chunk_preview = chunk[:200].decode('utf-8', errors='ignore')
                                logger.debug(
                                    "stream_chunk_received",
                                    chunk_num=chunk_count,
                                    chunk_size=len(chunk),
                                    preview=chunk_preview[:100]
                                )
                            # 收集少量数据用于后续监控（限制大小，不阻塞）
                            if len(collected_data) < 100 and len(b''.join(collected_data)) < 10000:
                                collected_data.append(chunk)
                            yield chunk
                    
                    # 记录流结束信息
                    logger.info(
                        "stream_completed",
                        total_chunks=chunk_count,
                        total_bytes=total_bytes,
                        has_data=chunk_count > 0
                    )
                    
                    # 如果没有收到任何数据块，记录警告
                    if chunk_count == 0:
                        logger.warning(
                            "stream_empty",
                            message="流式响应未收到任何数据块，可能vLLM服务立即返回了结束标记"
                        )
                    
                    stream_finished = True
                except (httpx.StreamClosed, httpx.CloseError) as e:
                    # 流正常关闭，这是预期行为，不需要记录为错误
                    logger.debug("stream_closed_normal", message="流式响应正常关闭")
                    stream_finished = True
                    pass
                except Exception as e:
                    logger.error("stream_error", error=str(e), error_type=type(e).__name__)
                    stream_finished = True
                    # 发送错误信息（SSE格式）
                    try:
                        error_msg = f"data: {{\"error\": \"流式响应错误: {str(e)}\"}}\n\n".encode('utf-8')
                        yield error_msg
                    except:
                        pass
                finally:
                    # 流结束后，异步处理监控数据（不阻塞响应）
                    if stream_finished:
                        asyncio.create_task(
                            process_stream_monitoring(collected_data.copy(), api_key, body)
                        )
    
    # 创建 StreamingResponse，生成器会在响应发送时执行
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff"
        }
    )


async def forward_non_stream_request(
    vllm_url: str,
    body: Dict[str, Any],
    api_key: str
) -> Dict[str, Any]:
    """转发非流式请求到vLLM"""
    logger.info(
        "forwarding_non_stream_request",
        vllm_url=vllm_url,
        model=body.get("model"),
        api_key=api_key[:8] + "..." if api_key else None
    )
    
    timeout_config = _get_timeout_config(is_stream=False)
    
    # 使用全局HTTP客户端（连接池复用）
    client = _get_http_client()
    response = await client.post(
        vllm_url,
        json=body,
        headers={
            "Content-Type": "application/json"
        },
        timeout=timeout_config
    )
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )
    
    # 安全解析JSON
    try:
        result = response.json()
    except Exception as json_error:
        response_text = response.text[:500] if response.text else "(空响应)"
        logger.error(
            "json_parse_error",
            error=str(json_error),
            response_status=response.status_code,
            response_preview=response_text,
            content_type=response.headers.get("content-type", "unknown")
        )
        raise HTTPException(
            status_code=500,
            detail=f"vLLM响应解析失败: {str(json_error)}. 响应预览: {response_text}"
        )
    
    # 记录token使用量
    if "usage" in result:
        usage = result["usage"]
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        record_token_usage(api_key, input_tokens, output_tokens)
        logger.debug(
            "vllm_response_received",
            input_tokens=input_tokens,
            output_tokens=output_tokens
        )
    
    logger.info("non_stream_request_completed", vllm_url=vllm_url)
    return result


async def forward_get_request(vllm_url: str, timeout: float = 30.0) -> Dict[str, Any]:
    """转发GET请求到vLLM"""
    logger.debug("forwarding_get_request", vllm_url=vllm_url)
    
    # 使用全局HTTP客户端（连接池复用）
    client = _get_http_client()
    response = await client.get(vllm_url, timeout=timeout)
    
    if response.status_code != 200:
        logger.error(
            "vllm_get_request_error",
            vllm_url=vllm_url,
            status_code=response.status_code
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )
    
    logger.debug("get_request_completed", vllm_url=vllm_url)
    return response.json()

