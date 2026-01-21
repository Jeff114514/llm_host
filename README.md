## 项目简介

**vLLM Proxy** 是一个基于 **FastAPI +（vLLM / sglang）+ Nginx** 的本地 LLM 部署与代理方案，提供：

- 基于文件的 **API Key 认证与 admin 权限管理**
- **QPS / 并发 / Token** 三级限流
- **Prometheus + Grafana** 指标与可视化
- **结构化 JSON 日志** 与自动日志轮转/清理
- 对 OpenAI 兼容接口的 **统一代理**（按 `model` 字段自动路由到对应后端）
- vLLM 的 **多 LoRA 管理**（可选）

整体架构：

```text
Client → Nginx (反向代理) → FastAPI (认证 / 限流 / 监控 / 模型路由) → vLLM 或 sglang (OpenAI API 兼容)
```

> 所有认证、限流、监控逻辑都集中在 FastAPI 层，Nginx 仅负责反向代理与网络边界。

## 模型路由（按 model 自动选择后端）

- FastAPI 会维护 **模型名 → 后端（vllm/sglang）** 的映射，并在 `/v1/chat/completions` 与 `/v1/completions` 中根据请求体的 `model` 字段自动路由。
- 映射来源：
  - `config/config.yaml` 中的 `model_backend_mapping`（手动配置，优先级最高）
  - 自动发现：从已启动的后端拉取 `/v1/models` 聚合模型列表

## 文档导航

**运行与运维相关内容均已迁移到 `docs/` 目录：**

- `docs/QUICKSTART.md`：快速开始（环境要求 / 安装依赖 / 启动与停止 / API 示例）
- `docs/API_REFERENCE.md`：对外接口与鉴权说明（Chat/Completion/Models/Admin）
- `docs/START_GUIDE.md`：启动脚本与 Python 启动器的详细说明
- `docs/LOG_ROTATION.md`：日志轮转与清理策略
- `docs/NGINX_SETUP.md`：Nginx 安装与反向代理配置
- `docs/MONITORING_SETUP.md`：Prometheus & Grafana 监控配置
- `docs/LORA_USAGE_EXAMPLES.md`：多 LoRA / 多模型使用示例

**各子模块的代码设计说明：**

- `app/README.md`：核心业务逻辑（配置加载、认证、限流、监控、vLLM 代理与进程管理）
- `config/README.md`：配置文件结构与字段含义
- `scripts/README.md`：脚本列表与职责（不含具体启动命令）
- `monitoring/README.md`：监控相关配置文件说明
- `nginx/README.md`：Nginx 配置文件结构与反向代理规则

## 目录结构（代码视角）

```text
LLMHOST/
├── app/                  # FastAPI 应用与业务逻辑
│   ├── main.py           # 应用装配 / 生命周期 / 全局组件初始化
│   ├── routes.py         # 所有 HTTP 路由与业务入口
│   ├── auth.py           # API Key 认证与 admin 权限
│   ├── limiter.py        # QPS / 并发 / Token 限流
│   ├── monitoring.py     # Prometheus 指标与结构化日志
│   ├── models.py         # 配置与领域模型（Pydantic）
│   ├── config_manager.py # 配置加载与全局单例
│   ├── vllm_client.py    # 调用 vLLM OpenAI 兼容接口
│   ├── vllm_manager.py   # vLLM 进程管理与 LoRA 环境注入
│   ├── sglang_client.py  # 调用 sglang OpenAI 兼容接口
│   ├── sglang_manager.py # sglang 进程管理
│   ├── model_router.py   # 模型名到后端的路由管理
│   └── log_manager.py    # 日志轮转 / 清理 / 统计
├── config/               # 配置文件
│   ├── config.yaml       # 应用主配置（端口 / 限流 / vLLM 启动方式等）
│   ├── api_keys.json     # API Key 列表
│   ├── vllm_start_cmd.txt# vLLM 启动命令模板（可选）
│   └── sglang_start_cmd.txt# sglang 启动命令模板（可选）
├── scripts/              # 启动 / 停止 / 日志管理脚本
│   ├── start.sh          # 一键启动 vLLM + FastAPI (+ Nginx 可选)
│   ├── stop.sh           # 一键停止所有组件
│   ├── start_vllm.py     # Python 方式管理 vLLM 进程
│   └── log_rotate.sh     # 日志轮转与清理辅助脚本
├── nginx/                # Nginx 反向代理配置
│   └── nginx.conf
├── monitoring/           # Prometheus / Grafana 配置与仪表盘
│   ├── prometheus.yml
│   └── grafana/dashboard.json
├── docs/                 # 所有运行与使用文档
├── logs/                 # 日志目录（运行时创建）
├── .pids/                # PID 目录（运行时创建）
├── requirements.txt      # Python 依赖
└── README.md             # 当前文件
```

## 技术栈一览

- **FastAPI**：Web 框架，承载所有业务逻辑（认证 / 限流 / 监控 / 代理）
- **vLLM / sglang**：LLM 推理服务，提供 OpenAI 兼容 API（FastAPI 层按 model 路由）
- **Nginx**：反向代理与边界层（不做认证与限流）
- **Prometheus + Grafana**：监控与可视化
- **slowapi**：基于 Starlette/FastAPI 的 QPS 限流组件
- **prometheus-fastapi-instrumentator**：自动暴露 FastAPI 指标
- **httpx**：高并发 HTTP 客户端
- **structlog**：结构化 JSON 日志
- **PyYAML / Pydantic**：配置加载与强类型建模

## 许可证

本项目采用 **MIT License** 开源许可。
