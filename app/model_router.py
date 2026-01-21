"""模型路由管理器：根据模型名称选择推理后端（vLLM / sglang），支持多个后端实例。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import httpx

from app.models import AppConfig, BackendType
from app.monitoring import logger


@dataclass(frozen=True)
class BackendEndpoint:
    backend: BackendType
    base_url: str  # e.g. http://localhost:8002
    instance_id: str  # 唯一标识，通常是 base_url


class ModelRouter:
    """维护模型名称 -> 后端实例 的映射，并负责聚合 /v1/models。
    
    支持多个后端实例，每个实例有唯一的 URL。模型可以映射到任意后端实例。
    """

    def __init__(self, config: AppConfig):
        self._config = config
        # 后端实例映射：instance_id -> BackendEndpoint
        self._backends: Dict[str, BackendEndpoint] = {}
        # 模型到后端实例的映射：model -> (backend_type, base_url)
        self._discovered: Dict[str, Tuple[BackendType, str]] = {}
        # 手动配置的模型映射：model -> (backend_type, base_url) 或 model -> backend_type
        self._manual: Dict[str, Tuple[BackendType, Optional[str]]] = {}
        
        # 解析手动映射配置
        if config.model_backend_mapping:
            for model, backend_type in config.model_backend_mapping.items():
                # 如果配置中没有指定 URL，使用 None，表示使用默认实例
                self._manual[model] = (backend_type, None)

        # 可选：注册默认后端实例（如果配置了）
        if config.vllm_host and config.vllm_port:
            default_vllm_url = f"http://{config.vllm_host}:{config.vllm_port}"
            self.register_backend(BackendType.VLLM, default_vllm_url)
        
        if config.sglang_host and config.sglang_port:
            default_sglang_url = f"http://{config.sglang_host}:{config.sglang_port}"
            self.register_backend(BackendType.SGLANG, default_sglang_url)

    def register_backend(self, backend: BackendType, base_url: str) -> str:
        """注册一个后端实例，返回实例 ID（base_url）"""
        base_url = base_url.rstrip("/")
        instance_id = base_url
        
        # 如果已存在相同 URL 的实例，直接返回
        if instance_id in self._backends:
            logger.debug("backend_already_registered", backend=backend.value, base_url=base_url)
            return instance_id
        
        endpoint = BackendEndpoint(backend=backend, base_url=base_url, instance_id=instance_id)
        self._backends[instance_id] = endpoint
        logger.info("backend_registered", backend=backend.value, base_url=base_url, instance_id=instance_id)
        return instance_id

    def unregister_backend(self, base_url: str) -> bool:
        """注销一个后端实例"""
        base_url = base_url.rstrip("/")
        instance_id = base_url
        
        if instance_id not in self._backends:
            logger.warning("backend_not_found", instance_id=instance_id)
            return False
        
        # 移除该实例的所有模型映射
        models_to_remove = [
            model for model, (_, url) in self._discovered.items()
            if url == base_url
        ]
        for model in models_to_remove:
            del self._discovered[model]
        
        del self._backends[instance_id]
        logger.info("backend_unregistered", instance_id=instance_id, removed_models=len(models_to_remove))
        return True

    def list_backends(self) -> List[Dict[str, str]]:
        """列出所有已注册的后端实例"""
        return [
            {
                "instance_id": endpoint.instance_id,
                "backend": endpoint.backend.value,
                "base_url": endpoint.base_url
            }
            for endpoint in self._backends.values()
        ]

    def update_manual_mapping(self, mapping: Dict[str, BackendType]) -> None:
        """更新手动模型映射（仅支持后端类型，不支持指定 URL）"""
        self._manual = {
            model: (backend_type, None)
            for model, backend_type in (mapping or {}).items()
        }
        logger.info("model_backend_mapping_updated", size=len(self._manual))

    def get_backend_for_model(self, model: str) -> Optional[Tuple[BackendType, str]]:
        """获取模型对应的后端类型和 URL。返回 (BackendType, base_url) 或 None"""
        if not model:
            return None
        
        # 先检查手动映射
        if model in self._manual:
            backend_type, url = self._manual[model]
            if url is not None:
                return (backend_type, url)
            # 如果手动映射中没有指定 URL，尝试找到该类型的默认实例
            # 查找第一个匹配的后端实例
            for endpoint in self._backends.values():
                if endpoint.backend == backend_type:
                    return (backend_type, endpoint.base_url)
            return None
        
        # 检查自动发现的映射
        return self._discovered.get(model)

    def get_base_url(self, backend: BackendType, instance_id: Optional[str] = None) -> Optional[str]:
        """获取后端的基础 URL。如果指定了 instance_id，返回该实例的 URL；否则返回第一个匹配的实例"""
        if instance_id:
            endpoint = self._backends.get(instance_id)
            if endpoint and endpoint.backend == backend:
                return endpoint.base_url
            return None
        
        # 返回第一个匹配的后端实例
        for endpoint in self._backends.values():
            if endpoint.backend == backend:
                return endpoint.base_url
        return None

    def build_url(self, backend_type: BackendType, path: str, base_url: Optional[str] = None) -> Optional[str]:
        """构建完整的请求 URL"""
        if base_url:
            url = base_url
        else:
            url = self.get_base_url(backend_type)
            if not url:
                return None
        
        if not path.startswith("/"):
            path = "/" + path
        return f"{url}{path}"

    async def refresh_models(self) -> Dict[str, Tuple[BackendType, str]]:
        """从所有后端实例拉取 /v1/models 并更新自动发现映射。失败的后端跳过。"""
        discovered: Dict[str, Tuple[BackendType, str]] = {}

        async with httpx.AsyncClient(timeout=10.0) as client:
            for instance_id, endpoint in self._backends.items():
                url = f"{endpoint.base_url}/v1/models"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(
                            "backend_models_fetch_failed",
                            backend=endpoint.backend.value,
                            instance_id=instance_id,
                            url=url,
                            status_code=resp.status_code,
                        )
                        continue
                    payload = resp.json()
                    model_ids = self._extract_model_ids(payload)
                    for mid in model_ids:
                        # 手动映射的模型优先级更高，跳过
                        if mid in self._manual:
                            continue
                        # 如果模型已发现，检查是否有冲突
                        if mid in discovered:
                            existing_backend, existing_url = discovered[mid]
                            if existing_backend != endpoint.backend or existing_url != endpoint.base_url:
                                logger.warning(
                                    "model_backend_conflict",
                                    model=mid,
                                    backend_a=existing_backend.value,
                                    url_a=existing_url,
                                    backend_b=endpoint.backend.value,
                                    url_b=endpoint.base_url,
                                )
                                continue
                        discovered[mid] = (endpoint.backend, endpoint.base_url)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "backend_models_fetch_error",
                        backend=endpoint.backend.value,
                        instance_id=instance_id,
                        url=url,
                        error=str(exc),
                    )
                    continue

        self._discovered = discovered
        logger.info(
            "model_router_refreshed",
            discovered_models=len(self._discovered),
            manual_models=len(self._manual),
            backend_instances=len(self._backends),
        )
        return dict(self._discovered)

    def list_models(self) -> List[str]:
        ids: Set[str] = set(self._discovered.keys()) | set(self._manual.keys())
        return sorted(ids)

    def list_models_openai_payload(self) -> Dict[str, object]:
        data = [{"id": mid, "object": "model"} for mid in self.list_models()]
        return {"object": "list", "data": data}

    @staticmethod
    def _extract_model_ids(payload: object) -> Iterable[str]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        ids: List[str] = []
        for item in data:
            if isinstance(item, dict):
                mid = item.get("id")
                if isinstance(mid, str) and mid:
                    ids.append(mid)
        return ids

