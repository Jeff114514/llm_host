"""sglang 客户端模块 - 复用 vLLM 客户端的通用转发逻辑。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi.responses import StreamingResponse

from app.vllm_client import (
    forward_get_request as _forward_get_request,
    forward_non_stream_request as _forward_non_stream_request,
    forward_stream_request as _forward_stream_request,
)


async def forward_stream_request(
    sglang_url: str, body: Dict[str, Any], api_key: str
) -> StreamingResponse:
    return await _forward_stream_request(sglang_url, body, api_key)


async def forward_non_stream_request(
    sglang_url: str, body: Dict[str, Any], api_key: str
) -> Dict[str, Any]:
    return await _forward_non_stream_request(sglang_url, body, api_key)


async def forward_get_request(sglang_url: str, timeout: float = 30.0) -> Dict[str, Any]:
    return await _forward_get_request(sglang_url, timeout=timeout)

