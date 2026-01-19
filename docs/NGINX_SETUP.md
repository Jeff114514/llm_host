# Nginx配置指南

## 安装Nginx

### Ubuntu/Debian
```bash
sudo apt update
sudo apt install nginx
```

### CentOS/RHEL
```bash
sudo yum install nginx
```

## 配置说明

### 方式1: 作为独立配置文件使用（推荐）

1. 测试配置：
```bash
nginx -t -c /path/to/LLMHOST/nginx/nginx.conf
```

2. 启动Nginx（使用指定配置文件）：
```bash
nginx -c /path/to/LLMHOST/nginx/nginx.conf
```

### 方式2: 集成到主Nginx配置

1. 将配置内容添加到主Nginx配置文件的`http`块内：
```bash
# 编辑主配置文件 /etc/nginx/nginx.conf
# 在http块内添加：
include /path/to/LLMHOST/nginx/nginx.conf;
```

2. 测试配置：
```bash
sudo nginx -t
```

3. 重启Nginx：
```bash
sudo systemctl restart nginx
```

**注意**: 如果使用方式2，需要修改nginx.conf文件，移除外层的`http`和`events`块，只保留`upstream`和`server`块内容。

## 配置特点

- **反向代理**: 所有请求转发到FastAPI（端口8001）
- **超时设置**: 300秒（适合LLM长文本生成）
- **流式支持**: 关闭缓冲以支持流式响应
- **注意**: 限流、认证等控制逻辑都在FastAPI中处理，Nginx仅作为反向代理

## 日志位置

- 访问日志: `/var/log/nginx/vllm_proxy_access.log`
- 错误日志: `/var/log/nginx/vllm_proxy_error.log`

## SSL配置

如需启用HTTPS，请取消注释HTTPS server块，并配置SSL证书路径。

## 优势

- 统一管理：所有控制逻辑集中在FastAPI
- 更灵活：可以动态调整限制策略
- 更精确：可以基于API key、用户等维度进行限制

## 故障排查

### Nginx启动失败

1. **检查配置文件**:
```bash
nginx -t -c /path/to/LLMHOST/nginx/nginx.conf
```

2. **检查端口占用**:
```bash
lsof -i :8000
```

3. **查看Nginx错误日志**:
```bash
tail -f /var/log/nginx/vllm_proxy_error.log
```

### 请求无法转发

1. 检查FastAPI服务是否运行在8001端口
2. 检查Nginx配置中的upstream设置
3. 查看Nginx访问日志和错误日志
