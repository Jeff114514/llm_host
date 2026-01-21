# Prometheus 指标查看指南

## 指标分类说明

### 1. Python 运行时指标（自动收集）

#### 垃圾回收（GC）指标
```
python_gc_objects_collected_total{generation="0"} 1173.0
python_gc_objects_collected_total{generation="1"} 112.0
python_gc_objects_collected_total{generation="2"} 6.0
```
- **含义**: 各代（0/1/2）垃圾回收收集的对象总数
- **用途**: 监控内存管理效率，generation 2 的回收频率低是正常的

#### Python 信息
```
python_info{implementation="CPython",major="3",minor="12",patchlevel="12",version="3.12.12"} 1.0
```
- **含义**: Python 版本信息
- **用途**: 确认运行环境版本

### 2. 进程资源指标（自动收集）

#### 内存使用
```
process_virtual_memory_bytes 3.93392128e+08    # 约 375 MB
process_resident_memory_bytes 6.9300224e+07    # 约 66 MB
```
- **含义**: 
  - `virtual_memory_bytes`: 虚拟内存大小（包含映射文件）
  - `resident_memory_bytes`: 实际物理内存占用（RSS）
- **用途**: 监控内存使用情况，防止内存泄漏

#### CPU 使用
```
process_cpu_seconds_total 32.61
```
- **含义**: 进程累计使用的 CPU 时间（秒）
- **用途**: 计算 CPU 使用率：`rate(process_cpu_seconds_total[5m])`

#### 文件描述符
```
process_open_fds 21.0
process_max_fds 1.048576e+06
```
- **含义**: 
  - `open_fds`: 当前打开的文件描述符数量
  - `max_fds`: 最大可打开的文件描述符数量
- **用途**: 监控文件句柄泄漏

#### 进程启动时间
```
process_start_time_seconds 1.76893503979e+09
```
- **含义**: 进程启动的 Unix 时间戳
- **用途**: 计算运行时长：`time() - process_start_time_seconds`

### 3. HTTP 请求指标（应用自定义）

#### 请求总数
```
http_requests_total{endpoint="/health",method="GET",status_code="200"} 1.0
http_requests_total{endpoint="/metrics",method="GET",status_code="200"} 2.0
http_requests_total{endpoint="/",method="GET",status_code="404"} 3.0
```
- **含义**: 按端点、方法、状态码统计的请求总数
- **用途**: 
  - 查看各端点的访问量
  - 识别 404 等错误端点
  - 计算请求速率：`rate(http_requests_total[5m])`

#### 请求持续时间（直方图）
```
http_request_duration_seconds_bucket{endpoint="/health",le="0.005",method="GET"} 1.0
http_request_duration_seconds_count{endpoint="/health",method="GET"} 1.0
http_request_duration_seconds_sum{endpoint="/health",method="GET"} 0.003093719482421875
```
- **含义**: 
  - `bucket`: 各时间区间的请求数量（le="0.005" 表示 ≤5ms）
  - `count`: 总请求数
  - `sum`: 总响应时间
- **用途**: 
  - 计算平均响应时间：`rate(http_request_duration_seconds_sum[5m]) / rate(http_request_duration_seconds_count[5m])`
  - 计算 P95/P99 延迟：`histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))`

#### 活跃请求数
```
http_active_requests 2.0
```
- **含义**: 当前正在处理的请求数量
- **用途**: 监控并发负载，值持续较高可能表示性能瓶颈

#### 错误统计
```
http_errors_total{endpoint="/",error_type="http_404",method="GET"} 3.0
http_errors_total{endpoint="/favicon.ico",error_type="http_404",method="GET"} 2.0
```
- **含义**: 按端点、方法、错误类型统计的错误总数
- **用途**: 
  - 识别问题端点
  - 计算错误率：`rate(http_errors_total[5m]) / rate(http_requests_total[5m])`

#### Token 使用量
```
token_usage_total{api_key="...",type="input"} 0
token_usage_total{api_key="...",type="output"} 0
```
- **含义**: 按 API key 和类型（input/output）统计的 Token 使用总量
- **用途**: 
  - 监控各用户的 Token 消耗
  - 计算 Token 使用速率：`rate(token_usage_total[5m])`
  - 成本核算和配额管理

## 常用 Prometheus 查询（PromQL）

### 1. 请求速率（每秒请求数）
```promql
# 所有请求的总速率
sum(rate(http_requests_total[5m]))

# 按端点分组的请求速率
sum by (endpoint) (rate(http_requests_total[5m]))

# 特定端点的请求速率
sum(rate(http_requests_total{endpoint="/v1/chat/completions"}[5m]))
```

### 2. 平均响应时间
```promql
# 所有端点的平均响应时间
sum(rate(http_request_duration_seconds_sum[5m])) / sum(rate(http_request_duration_seconds_count[5m]))

# 按端点分组的平均响应时间
sum by (endpoint) (rate(http_request_duration_seconds_sum[5m])) / 
sum by (endpoint) (rate(http_request_duration_seconds_count[5m]))
```

### 3. P95/P99 延迟
```promql
# P95 延迟（95% 的请求在此时间内完成）
histogram_quantile(0.95, 
  sum by (endpoint, le) (rate(http_request_duration_seconds_bucket[5m]))
)

# P99 延迟
histogram_quantile(0.99, 
  sum by (endpoint, le) (rate(http_request_duration_seconds_bucket[5m]))
)
```

### 4. 错误率
```promql
# 总体错误率
sum(rate(http_errors_total[5m])) / sum(rate(http_requests_total[5m])) * 100

# 按端点分组的错误率
sum by (endpoint) (rate(http_errors_total[5m])) / 
sum by (endpoint) (rate(http_requests_total[5m])) * 100
```

### 5. 内存使用率
```promql
# 当前内存使用（MB）
process_resident_memory_bytes / 1024 / 1024

# 内存使用率（如果知道总内存）
process_resident_memory_bytes / (总内存字节数) * 100
```

### 6. CPU 使用率
```promql
# CPU 使用率（百分比）
rate(process_cpu_seconds_total[5m]) * 100
```

### 7. Token 使用速率
```promql
# 总 Token 使用速率（每秒）
sum(rate(token_usage_total[5m]))

# 按 API key 分组的 Token 使用速率
sum by (api_key) (rate(token_usage_total[5m]))

# 输入 Token 速率
sum(rate(token_usage_total{type="input"}[5m]))

# 输出 Token 速率
sum(rate(token_usage_total{type="output"}[5m]))
```

### 8. 活跃请求数趋势
```promql
# 当前活跃请求数
http_active_requests

# 活跃请求数的平均值（5分钟）
avg_over_time(http_active_requests[5m])
```

## 查看方式

### 方式 1: 直接访问 `/metrics` 端点
```bash
# 浏览器访问
http://localhost:8001/metrics

# 或使用 curl
curl http://localhost:8001/metrics
```

### 方式 2: 使用 Prometheus Web UI
1. 启动 Prometheus（参考 `docs/MONITORING_SETUP.md`）
2. 访问 `http://localhost:9090`
3. 在 "Graph" 页面输入 PromQL 查询
4. 查看图表和表格结果

### 方式 3: 使用 Grafana 仪表板
1. 启动 Grafana（参考 `docs/MONITORING_SETUP.md`）
2. 配置 Prometheus 数据源
3. 导入 `monitoring/grafana/dashboard.json`
4. 查看可视化图表和面板

### 方式 4: 使用命令行工具
```bash
# 使用 promtool（Prometheus 自带）
promtool query instant 'http://localhost:8001/metrics' 'rate(http_requests_total[5m])'

# 使用 curl + jq 解析
curl -s http://localhost:8001/metrics | grep http_requests_total
```

## 指标解读示例

根据您提供的指标数据：

### 当前状态分析

1. **请求统计**:
   - `/health`: 1 次成功请求（200）
   - `/metrics`: 2 次成功请求（200）
   - `/`: 3 次 404 错误（可能是根路径访问）
   - `/favicon.ico`: 2 次 404（浏览器自动请求）
   - `/admin/reload-keys`: 1 次成功请求（200）

2. **响应时间**:
   - `/health`: 平均 3.09ms（非常快）
   - `/metrics`: 平均 5.09ms（正常）
   - 所有请求都在 10ms 以内，性能良好

3. **活跃请求**: 2.0
   - 表示当前有 2 个请求正在处理（可能是正在访问 `/metrics`）

4. **错误情况**:
   - 主要是 404 错误，来自根路径和 favicon 请求
   - 这些是正常的，不影响核心功能

5. **Token 使用**: 暂无数据
   - 说明还没有处理过聊天完成请求

### 建议关注点

1. **监控 404 错误**: 如果根路径 `/` 频繁被访问，可以考虑添加重定向或健康检查页面
2. **Token 使用**: 当开始处理实际请求后，关注 `token_usage_total` 指标
3. **响应时间**: 当请求量增加时，关注 P95/P99 延迟是否在可接受范围内
4. **内存使用**: 当前约 66MB，如果持续增长需要关注内存泄漏

## 告警规则示例

可以在 Prometheus 中配置以下告警规则：

```yaml
groups:
  - name: fastapi_alerts
    rules:
      # 错误率过高
      - alert: HighErrorRate
        expr: sum(rate(http_errors_total[5m])) / sum(rate(http_requests_total[5m])) > 0.05
        for: 5m
        annotations:
          summary: "错误率超过 5%"
      
      # 响应时间过长
      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1
        for: 5m
        annotations:
          summary: "P95 延迟超过 1 秒"
      
      # 内存使用过高
      - alert: HighMemoryUsage
        expr: process_resident_memory_bytes > 1073741824  # 1GB
        for: 5m
        annotations:
          summary: "内存使用超过 1GB"
      
      # 活跃请求数过高
      - alert: HighConcurrency
        expr: http_active_requests > 100
        for: 5m
        annotations:
          summary: "并发请求数超过 100"
```

## 相关文档

- `docs/MONITORING_SETUP.md`: Prometheus 和 Grafana 安装配置指南
- `monitoring/prometheus.yml`: Prometheus 配置文件
- `monitoring/grafana/dashboard.json`: Grafana 仪表板配置
- `app/monitoring.py`: 指标定义代码
