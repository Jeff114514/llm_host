## 概览

`nginx` 目录包含 **Nginx 反向代理配置文件**，用于在 FastAPI 服务前提供网络边界层。  
本文件只说明配置文件的结构与反向代理规则，如何安装、配置与启动 Nginx 请参考：

- `docs/NGINX_SETUP.md`

## 文件结构

```text
nginx/
└── nginx.conf  # Nginx 反向代理配置
```

## `nginx.conf` – Nginx 配置

该文件定义了 Nginx 作为反向代理的完整配置。

### 主要配置项

- **upstream 块**
  - 定义后端 FastAPI 服务地址（默认 `localhost:8001`）
- **server 块**
  - `listen`: 监听端口（默认 `8000`）
  - `location /`: 将所有请求转发到 upstream
  - **超时设置**: 300 秒（适合 LLM 长文本生成）
  - **流式支持**: 关闭缓冲以支持流式响应（SSE）
  - **日志配置**: 访问日志与错误日志路径

### 设计原则

- **纯反向代理**: Nginx 不进行任何认证、限流或业务逻辑处理
- **所有控制逻辑在 FastAPI**: 认证、限流、监控等都在 FastAPI 层实现
- **优势**: 
  - 统一管理：所有控制逻辑集中在 FastAPI
  - 更灵活：可以动态调整限制策略
  - 更精确：可以基于 API key、用户等维度进行限制

### 日志位置

- 访问日志: `/var/log/nginx/vllm_proxy_access.log`
- 错误日志: `/var/log/nginx/vllm_proxy_error.log`

### SSL 配置

- 如需启用 HTTPS，请取消注释 HTTPS server 块，并配置 SSL 证书路径
