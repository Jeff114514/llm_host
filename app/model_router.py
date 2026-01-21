"""模型路由管理器：根据模型名称选择推理后端（vLLM / sglang）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

import httpx

from app.models import AppConfig, BackendType
from app.monitoring import logger


@dataclass(frozen=True)
class BackendEndpoint:
    backend: BackendType
    base_url: str  # e.g. http://localhost:8002


class ModelRouter:
    """维护模型名称 -> 后端 的映射，并负责聚合 /v1/models。"""

    def __init__(self, config: AppConfig):
        self._config = config
        self._backends: Dict[BackendType, BackendEndpoint] = {}
        self._discovered: Dict[str, BackendType] = {}
        self._manual: Dict[str, BackendType] = dict(config.model_backend_mapping or {})

        # 默认注册两个后端（即使未启动，refresh 时会忽略不可达）
        self.register_backend(
            BackendType.VLLM, f"http://{config.vllm_host}:{config.vllm_port}"
        )
        self.register_backend(
            BackendType.SGLANG, f"http://{config.sglang_host}:{config.sglang_port}"
        )

    def register_backend(self, backend: BackendType, base_url: str) -> None:
        base_url = base_url.rstrip("/")
        self._backends[backend] = BackendEndpoint(backend=backend, base_url=base_url)
        logger.info("backend_registered", backend=backend.value, base_url=base_url)

    def update_manual_mapping(self, mapping: Dict[str, BackendType]) -> None:
        self._manual = dict(mapping or {})
        logger.info("model_backend_mapping_updated", size=len(self._manual))

    def get_backend_for_model(self, model: str) -> Optional[BackendType]:
        if not model:
            return None
        if model in self._manual:
            return self._manual[model]
        return self._discovered.get(model)

    def get_base_url(self, backend: BackendType) -> str:
        return self._backends[backend].base_url

    def build_url(self, backend: BackendType, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.get_base_url(backend)}{path}"

    async def refresh_models(self) -> Dict[str, BackendType]:
        """从所有后端拉取 /v1/models 并更新自动发现映射。失败的后端跳过。"""
        discovered: Dict[str, BackendType] = {}

        async with httpx.AsyncClient(timeout=10.0) as client:
            for backend, endpoint in self._backends.items():
                url = f"{endpoint.base_url}/v1/models"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(
                            "backend_models_fetch_failed",
                            backend=backend.value,
                            url=url,
                            status_code=resp.status_code,
                        )
                        continue
                    payload = resp.json()
                    model_ids = self._extract_model_ids(payload)
                    for mid in model_ids:
                        if mid in self._manual:
                            continue
                        if mid in discovered and discovered[mid] != backend:
                            logger.warning(
                                "model_backend_conflict",
                                model=mid,
                                backend_a=discovered[mid].value,
                                backend_b=backend.value,
                            )
                            continue
                        discovered[mid] = backend
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "backend_models_fetch_error",
                        backend=backend.value,
                        url=url,
                        error=str(exc),
                    )
                    continue

        self._discovered = discovered
        logger.info(
            "model_router_refreshed",
            discovered_models=len(self._discovered),
            manual_models=len(self._manual),
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

