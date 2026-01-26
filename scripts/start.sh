#!/bin/bash

# vLLM Proxy 启动脚本
# 使用方法: ./scripts/start.sh
# 注意: vLLM 和 sglang 由 FastAPI 根据 config.yaml 中的 auto_start 配置自动启动

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# PID文件目录
PID_DIR="$PROJECT_DIR/.pids"
mkdir -p "$PID_DIR"

# 日志目录
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

# 检查Python环境
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo -e "${RED}错误: 未找到$PYTHON_BIN${NC}"
    exit 1
fi

# Conda环境配置
CONDA_ENV="${CONDA_ENV:-Jeff-py312}"

# 检查conda是否可用
if command -v conda &> /dev/null; then
    CONDA_AVAILABLE=true
    echo -e "${GREEN}检测到Conda环境: $CONDA_ENV${NC}"
else
    CONDA_AVAILABLE=false
    echo -e "${YELLOW}警告: 未找到conda，将使用系统Python${NC}"
fi

# 加载配置
if [ -f "config/config.yaml" ]; then
    VLLM_HOST=$(grep "vllm_host:" config/config.yaml | awk '{print $2}' | tr -d '"' || echo "localhost")
    VLLM_PORT=$(grep "vllm_port:" config/config.yaml | awk '{print $2}' | tr -d '"' || echo "8002")
    SGLANG_HOST=$(grep "sglang_host:" config/config.yaml | awk '{print $2}' | tr -d '"' || echo "localhost")
    SGLANG_PORT=$(grep "sglang_port:" config/config.yaml | awk '{print $2}' | tr -d '"' || echo "8003")
    FASTAPI_PORT=$(grep "fastapi_port:" config/config.yaml | awk '{print $2}' | tr -d '"' || echo "8001")
else
    VLLM_HOST="localhost"
    VLLM_PORT=8002
    SGLANG_HOST="localhost"
    SGLANG_PORT=8003
    FASTAPI_PORT=8001
fi

# 停止占用端口的进程
stop_port_process() {
    local port=$1
    local service=$2
    # 将服务名转换为小写并映射到PID文件名
    local service_lower=$(echo "$service" | tr '[:upper:]' '[:lower:]')
    local pid_file="$PID_DIR/${service_lower}.pid"
    
    # 查找占用端口的进程
    local pid=$(lsof -ti :$port | head -n 1)
    
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}检测到端口 $port 被进程 $pid 占用${NC}"
        
        # 检查是否是我们的进程（通过PID文件）
        if [ -f "$pid_file" ]; then
            local saved_pid=$(cat "$pid_file")
            if [ "$pid" = "$saved_pid" ]; then
                echo -e "${GREEN}这是之前启动的$service进程，正在停止...${NC}"
                kill $pid 2>/dev/null || true
                sleep 2
                if ps -p $pid > /dev/null 2>&1; then
                    kill -9 $pid 2>/dev/null || true
                fi
                rm -f "$pid_file"
                sleep 1
                return 0
            fi
        fi
        
        # 询问是否停止
        read -p "是否停止占用端口 $port 的进程 $pid? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo -e "${GREEN}正在停止进程 $pid...${NC}"
            kill $pid 2>/dev/null || true
            sleep 2
            if ps -p $pid > /dev/null 2>&1; then
                kill -9 $pid 2>/dev/null || true
            fi
            rm -f "$pid_file"
            sleep 1
            return 0
        else
            echo -e "${RED}无法启动$service，端口被占用${NC}"
            return 1
        fi
    fi
    return 0
}

# 检查端口占用
check_port() {
    local port=$1
    local service=$2
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo -e "${YELLOW}警告: 端口 $port 已被占用${NC}"
        stop_port_process $port $service
        if [ $? -ne 0 ]; then
            return 1
        fi
    fi
    return 0
}

# 启动FastAPI
start_fastapi() {
    echo -e "${GREEN}启动FastAPI服务...${NC}"
    if ! check_port $FASTAPI_PORT "FastAPI"; then
        echo -e "${RED}无法启动FastAPI，端口被占用${NC}"
        return 1
    fi
    
    # 启动前检查并轮转日志
    if [ -f "$LOG_DIR/fastapi.log" ]; then
        local log_size=$(stat -f%z "$LOG_DIR/fastapi.log" 2>/dev/null || stat -c%s "$LOG_DIR/fastapi.log" 2>/dev/null || echo 0)
        local max_size=$((100 * 1024 * 1024))  # 100MB
        if [ "$log_size" -gt "$max_size" ]; then
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            mv "$LOG_DIR/fastapi.log" "$LOG_DIR/fastapi.log.$timestamp" 2>/dev/null || true
            echo -e "${GREEN}轮转FastAPI日志文件（大小: $((log_size / 1024 / 1024))MB）${NC}"
        fi
    fi
    
    # 确保日志文件存在
    touch "$LOG_DIR/fastapi.log"
    
    # 检查 stdbuf 是否可用
    if command -v stdbuf &> /dev/null; then
        STDBUF_CMD="stdbuf -oL -eL"
    else
        STDBUF_CMD=""
        echo -e "${YELLOW}警告: stdbuf 不可用，日志可能缓冲${NC}"
    fi
    
    # 启动FastAPI
    # 注意：使用 --workers 时，uvicorn 会 fork 多个进程，需要使用 stdbuf 确保日志正确输出
    # 使用nohup确保输出被正确重定向到日志文件
    # 设置PYTHONUNBUFFERED=1禁用Python缓冲，确保日志实时写入
    if [ "$CONDA_AVAILABLE" = true ]; then
        # 使用conda run，不需要activate
        # 设置环境变量禁用Python缓冲
        if [ -n "$STDBUF_CMD" ]; then
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "$STDBUF_CMD conda run -n $CONDA_ENV uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        else
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "conda run -n $CONDA_ENV uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        fi
    elif [ -d "venv" ]; then
        if [ -n "$STDBUF_CMD" ]; then
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "source venv/bin/activate && $STDBUF_CMD uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        else
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        fi
    elif [ -d ".venv" ]; then
        if [ -n "$STDBUF_CMD" ]; then
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "source .venv/bin/activate && $STDBUF_CMD uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        else
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        fi
    else
        if [ -n "$STDBUF_CMD" ]; then
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "$STDBUF_CMD uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        else
            nohup env PYTHONUNBUFFERED=1 PYTHONPATH="$PROJECT_DIR" bash -c "uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --log-level info --workers 4" >> "$LOG_DIR/fastapi.log" 2>&1 &
        fi
    fi
    FASTAPI_PID=$!
    echo $FASTAPI_PID > "$PID_DIR/fastapi.pid"
    
    # 记录启动信息到日志文件
    {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========================================"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动FastAPI服务"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] PID: $FASTAPI_PID"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 端口: $FASTAPI_PORT"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ========================================"
    } >> "$LOG_DIR/fastapi.log"
    
    # 验证进程是否启动
    sleep 1
    if command -v ps &> /dev/null && ps -p $FASTAPI_PID > /dev/null 2>&1; then
        echo -e "${GREEN}FastAPI已启动，PID: $FASTAPI_PID${NC}"
    elif command -v tasklist &> /dev/null && tasklist //FI "PID eq $FASTAPI_PID" 2>/dev/null | grep -q "$FASTAPI_PID"; then
        echo -e "${GREEN}FastAPI已启动，PID: $FASTAPI_PID${NC}"
    else
        echo -e "${YELLOW}警告: 无法验证FastAPI进程状态，PID: $FASTAPI_PID${NC}"
        echo -e "${YELLOW}请检查日志文件: $LOG_DIR/fastapi.log${NC}"
        if [ -f "$LOG_DIR/fastapi.log" ] && [ -s "$LOG_DIR/fastapi.log" ]; then
            echo "最近的日志内容:"
            tail -n 20 "$LOG_DIR/fastapi.log"
        fi
    fi
    echo "日志文件: $LOG_DIR/fastapi.log"
    
    # 等待FastAPI启动
    echo "等待FastAPI服务就绪..."
    for i in {1..15}; do
        if curl -s "http://localhost:$FASTAPI_PORT/health" > /dev/null 2>&1; then
            echo -e "${GREEN}FastAPI服务已就绪${NC}"
            return 0
        fi
        sleep 1
    done
    
    echo -e "${RED}警告: FastAPI服务可能未正常启动，请检查日志${NC}"
    return 1
}

# 启动Nginx（可选）
start_nginx() {
    if ! command -v nginx &> /dev/null; then
        echo -e "${YELLOW}跳过Nginx启动（未安装Nginx）${NC}"
        return 0
    fi
    
    NGINX_CONF="$PROJECT_DIR/nginx/nginx.conf"
    
    if [ ! -f "$NGINX_CONF" ]; then
        echo -e "${RED}错误: Nginx配置文件不存在: $NGINX_CONF${NC}"
        return 1
    fi
    
    echo -e "${GREEN}检查Nginx配置...${NC}"
    
    # 检查Nginx配置
    if nginx -t -c "$NGINX_CONF" 2>&1 | grep -q "successful"; then
        echo -e "${GREEN}Nginx配置检查通过${NC}"
    else
        echo -e "${RED}错误: Nginx配置检查失败${NC}"
        nginx -t -c "$NGINX_CONF"
        return 1
    fi
    
    # 检查是否已有nginx进程在使用该配置文件
    if [ -f "$PID_DIR/nginx.pid" ]; then
        local pid=$(cat "$PID_DIR/nginx.pid")
        if ps -p $pid > /dev/null 2>&1; then
            # 检查该进程是否使用我们的配置文件
            if ps -p $pid -o args= | grep -q "$NGINX_CONF"; then
                echo -e "${YELLOW}Nginx已在运行（PID: $pid）${NC}"
                return 0
            fi
        fi
    fi
    
    # 检查端口8000是否被占用
    if ! check_port 8000 "nginx"; then
        read -p "是否继续启动Nginx? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    echo -e "${GREEN}启动Nginx...${NC}"
    # 启动Nginx（后台运行）
    nginx -c "$NGINX_CONF"
    sleep 2
    
    # 查找Nginx主进程PID
    NGINX_PID=$(ps aux | grep "nginx: master process.*$NGINX_CONF" | grep -v grep | awk '{print $2}' | head -n 1)
    
    if [ -z "$NGINX_PID" ]; then
        # 如果找不到，尝试通过端口查找
        NGINX_PID=$(lsof -ti :8000 | head -n 1)
    fi
    
    # 验证Nginx是否成功启动
    if [ -n "$NGINX_PID" ] && ps -p $NGINX_PID > /dev/null 2>&1; then
        echo $NGINX_PID > "$PID_DIR/nginx.pid"
        echo -e "${GREEN}Nginx已启动，PID: $NGINX_PID${NC}"
        echo "配置文件: $NGINX_CONF"
        
        # 等待Nginx就绪
        echo "等待Nginx服务就绪..."
        for i in {1..10}; do
            if curl -s "http://localhost:8000/health" > /dev/null 2>&1; then
                echo -e "${GREEN}Nginx服务已就绪${NC}"
                return 0
            fi
            sleep 1
        done
        echo -e "${YELLOW}警告: Nginx可能未完全就绪，但进程已启动${NC}"
        return 0
    else
        echo -e "${RED}错误: Nginx启动失败，请检查日志${NC}"
        return 1
    fi
}

# 主流程
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  vLLM Proxy 启动脚本${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${GREEN}注意: vLLM 和 sglang 将根据 config.yaml 中的 auto_start 配置由 FastAPI 自动启动${NC}"
    echo ""
    
    # 启动 FastAPI（如果 auto_start=true，会自动启动 vLLM 和 sglang）
    start_fastapi
    sleep 2
    
    # 清理旧日志（7天前）
    echo -e "${GREEN}清理7天前的旧日志...${NC}"
    if [ -f "$SCRIPT_DIR/log_rotate.sh" ]; then
        bash "$SCRIPT_DIR/log_rotate.sh" "$LOG_DIR" 100 7
    else
        echo -e "${YELLOW}日志轮转脚本未找到，跳过自动清理${NC}"
    fi
    
    read -p "是否启动Nginx? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        start_nginx
    fi
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  启动完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "服务状态:"
    echo "  - FastAPI: http://localhost:$FASTAPI_PORT"
    echo "  - vLLM:    http://$VLLM_HOST:$VLLM_PORT (由 FastAPI 根据 auto_start 配置自动启动)"
    echo "  - sglang:  http://$SGLANG_HOST:$SGLANG_PORT (由 FastAPI 根据 auto_start 配置自动启动)"
    echo "  - Nginx:   http://localhost:8000"
    echo ""
    echo "PID文件: $PID_DIR/"
    echo "日志文件: $LOG_DIR/"
    echo ""
    echo "停止服务: ./scripts/stop.sh"
}

main


