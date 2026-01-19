## 概览

`monitoring` 目录包含 **Prometheus 与 Grafana 的配置文件**，用于监控 vLLM Proxy 的运行指标。  
本文件只说明配置文件的结构与作用，如何安装、启动与配置这些监控工具请参考：

- `docs/MONITORING_SETUP.md`

## 文件结构

```text
monitoring/
├── prometheus.yml      # Prometheus 抓取配置
└── grafana/
    └── dashboard.json  # Grafana 仪表板定义
```

## `prometheus.yml` – Prometheus 配置

该文件用于配置 Prometheus 如何抓取 FastAPI 服务暴露的指标。

### 主要配置项

- `scrape_interval`: 指标抓取间隔（默认 15 秒）
- `scrape_configs`: 抓取目标列表
  - `job_name`: 任务名称（如 `vllm_proxy`）
  - `static_configs.targets`: FastAPI 服务地址列表（默认 `localhost:8001`）

### 指标端点

- FastAPI 通过 `prometheus-fastapi-instrumentator` 自动暴露 `/metrics` 端点
- Prometheus 会定期访问该端点，收集以下指标：
  - `http_requests_total`: HTTP 请求总数（按方法、端点、状态码）
  - `http_request_duration_seconds`: 请求持续时间（直方图）
  - `http_active_requests`: 活跃请求数（仪表）
  - `http_errors_total`: 错误总数（按错误类型）
  - `token_usage_total`: Token 使用总量（按 API key 和类型）

## `grafana/dashboard.json` – Grafana 仪表板

该文件定义了 Grafana 仪表板的完整配置，包括：

- **数据源**: 指向 Prometheus（需在 Grafana 中手动配置）
- **面板定义**: 各种图表与指标展示
  - 请求总数与速率
  - 平均响应时间（P95）
  - 活跃请求数
  - 错误率
  - Token 使用量
  - 请求分布（按状态码）

### 使用方式

- 在 Grafana Web UI 中通过 "Import Dashboard" 功能导入该 JSON 文件
- 导入后选择对应的 Prometheus 数据源即可查看监控面板

## 相关代码

- `app/monitoring.py`: 定义了所有 Prometheus 指标与监控中间件
- `app/main.py`: 通过 `Instrumentator` 自动暴露 `/metrics` 端点
