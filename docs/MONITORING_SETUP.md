# 监控配置指南

## Prometheus配置

### 安装Prometheus

1. 下载Prometheus: https://prometheus.io/download/
```bash
wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz
tar xvfz prometheus-*.tar.gz
cd prometheus-*
```

2. 复制配置文件:
```bash
cp ../monitoring/prometheus.yml /path/to/prometheus/prometheus.yml
```

3. 启动Prometheus:
```bash
./prometheus --config.file=prometheus.yml
```

或者使用systemd服务（推荐）:
```bash
# 创建systemd服务文件 /etc/systemd/system/prometheus.service
sudo systemctl enable prometheus
sudo systemctl start prometheus
```

### 访问Prometheus
- Web UI: http://localhost:9090
- 指标查询: http://localhost:9090/graph

### 配置说明
- `scrape_interval`: 指标抓取间隔（15秒）
- `targets`: FastAPI服务地址（localhost:8001）

## Grafana配置

### 安装Grafana

#### Ubuntu/Debian
```bash
sudo apt-get install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
sudo apt-get update
sudo apt-get install grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
```

#### CentOS/RHEL
```bash
sudo yum install grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
```

#### 手动安装
1. 下载Grafana: https://grafana.com/grafana/download
2. 解压并启动:
```bash
tar -zxvf grafana-*.tar.gz
cd grafana-*
./bin/grafana-server
```

3. 访问 http://localhost:3000
4. 默认用户名/密码: admin/admin（首次登录会要求修改密码）

### 配置数据源

1. 登录Grafana
2. 进入 Configuration > Data Sources
3. 添加Prometheus数据源:
   - URL: http://localhost:9090
   - Access: Server (default)

### 导入仪表板

1. 进入 Dashboards > Import
2. 上传 `monitoring/grafana/dashboard.json` 文件
3. 选择Prometheus数据源
4. 点击Import

### 监控指标说明

- **请求总数**: HTTP请求速率（请求/秒）
- **平均响应时间**: P95响应时间
- **活跃请求数**: 当前正在处理的请求数
- **错误率**: HTTP错误请求占比
- **Token使用量**: 输入/输出Token使用速率
- **请求分布**: 按状态码的请求分布

## 监控指标列表

### HTTP指标
- `http_requests_total`: 总请求数（按方法、端点、状态码）
- `http_request_duration_seconds`: 请求持续时间（直方图）
- `http_active_requests`: 活跃请求数（仪表）
- `http_errors_total`: 错误总数（按错误类型）

### Token指标
- `token_usage_total`: Token使用总量（按API key和类型）

### 访问指标
- FastAPI 指标：`GET http://localhost:8001/metrics`
- Prometheus Web UI：`http://localhost:9090`

## 告警配置（可选）

可以在Prometheus中配置告警规则，例如：
- 错误率超过5%
- 响应时间超过10秒
- Token使用量异常

## 故障排查

1. **Prometheus无法抓取指标**
   - 检查FastAPI服务是否运行在8001端口
   - 检查 `/metrics` 端点是否可访问
   - 查看Prometheus日志

2. **Grafana无法连接Prometheus**
   - 检查Prometheus是否运行
   - 检查数据源URL配置
   - 检查网络连接

3. **仪表板无数据**
   - 确认Prometheus正在抓取指标
   - 检查时间范围设置
   - 验证指标名称是否正确
