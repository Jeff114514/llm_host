#!/bin/bash

# vLLM Proxy 停止脚本

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
PYTHON_BIN="${PYTHON_BIN:-python3}"
VLLM_PYTHON_LAUNCHER="$PROJECT_DIR/scripts/start_vllm.py"
SGLANG_PYTHON_LAUNCHER="$PROJECT_DIR/scripts/start_sglang.py"
CONDA_ENV="${CONDA_ENV:-Jeff-py312}"
if command -v conda &> /dev/null; then
    CONDA_AVAILABLE=true
else
    CONDA_AVAILABLE=false
fi

# 加载配置
if [ -f "config/config.yaml" ]; then
    FASTAPI_PORT=$(grep "fastapi_port:" config/config.yaml | awk '{print $2}' | tr -d '"')
    VLLM_PORT=$(grep "vllm_port:" config/config.yaml | awk '{print $2}' | tr -d '"')
    SGLANG_PORT=$(grep "sglang_port:" config/config.yaml | awk '{print $2}' | tr -d '"')
else
    FASTAPI_PORT=8001
    VLLM_PORT=8002
    SGLANG_PORT=8003
fi

# 检测操作系统类型
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" || "$OSTYPE" == "cygwin" ]]; then
    IS_WINDOWS=true
else
    IS_WINDOWS=false
fi

# 检查进程是否运行
is_process_running() {
    local pid=$1
    if [ "$IS_WINDOWS" = true ]; then
        if command -v tasklist &> /dev/null; then
            tasklist //FI "PID eq $pid" 2>/dev/null | grep -q "$pid"
        else
            return 1
        fi
    else
        if command -v ps &> /dev/null; then
            ps -p $pid > /dev/null 2>&1
        else
            return 1
        fi
    fi
}

# 杀死进程（跨平台）
kill_process() {
    local pid=$1
    local force=${2:-false}
    
    if [ "$IS_WINDOWS" = true ]; then
        if command -v taskkill &> /dev/null; then
            if [ "$force" = true ]; then
                taskkill //F //PID $pid 2>/dev/null || true
            else
                taskkill //PID $pid 2>/dev/null || true
            fi
        fi
    else
        if [ "$force" = true ]; then
            kill -9 $pid 2>/dev/null || true
        else
            kill $pid 2>/dev/null || true
        fi
    fi
}

# 通过端口查找进程
find_process_by_port() {
    local port=$1
    if command -v lsof &> /dev/null; then
        lsof -ti :$port 2>/dev/null | head -n 1
    elif command -v netstat &> /dev/null; then
        # Windows netstat 格式
        netstat -ano | grep ":$port " | grep LISTENING | awk '{print $5}' | head -n 1
    else
        echo ""
    fi
}

# 停止服务函数（通过PID文件）
stop_service() {
    local service_name=$1
    local pid_file="$PID_DIR/$service_name.pid"
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if is_process_running $pid; then
            echo -e "${GREEN}停止 $service_name (PID: $pid)...${NC}"
            kill_process $pid false
            sleep 2
            
            # 如果还在运行，强制杀死
            if is_process_running $pid; then
                echo -e "${YELLOW}强制停止 $service_name...${NC}"
                kill_process $pid true
                sleep 1
            fi
            
            rm -f "$pid_file"
            echo -e "${GREEN}$service_name 已停止${NC}"
        else
            echo -e "${YELLOW}$service_name 未运行（PID文件存在但进程不存在）${NC}"
            rm -f "$pid_file"
        fi
    else
        echo -e "${YELLOW}$service_name 未运行（未找到PID文件）${NC}"
    fi
}

# 停止所有FastAPI worker进程
stop_fastapi_workers() {
    echo -e "${GREEN}查找并停止所有FastAPI worker进程...${NC}"
    
    # 通过进程名查找
    if [ "$IS_WINDOWS" = true ]; then
        # Windows: 使用 tasklist 和 findstr
        if command -v tasklist &> /dev/null; then
            local pids=$(tasklist //FI "IMAGENAME eq python.exe" //FO CSV 2>/dev/null | grep -i "uvicorn\|app.main" | awk -F',' '{print $2}' | tr -d '"' || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止FastAPI worker (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止FastAPI worker (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    else
        # Linux/Mac: 使用 pgrep
        if command -v pgrep &> /dev/null; then
            local pids=$(pgrep -f "uvicorn.*app.main:app" 2>/dev/null || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止FastAPI worker (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止FastAPI worker (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    fi
    
    # 通过端口查找（备用方案）
    local port_pid=$(find_process_by_port $FASTAPI_PORT)
    if [ -n "$port_pid" ] && is_process_running $port_pid; then
        echo -e "${GREEN}通过端口 $FASTAPI_PORT 找到进程 (PID: $port_pid)，正在停止...${NC}"
        kill_process $port_pid false
        sleep 2
        if is_process_running $port_pid; then
            kill_process $port_pid true
        fi
    fi
}

# 通过Python管理器停止vLLM
stop_vllm_python() {
    if [ ! -f "$VLLM_PYTHON_LAUNCHER" ]; then
        return 1
    fi
    local -a cmd
    if [ "$CONDA_AVAILABLE" = true ]; then
        # 确保无论从哪里执行，都能 import app（避免 ModuleNotFoundError: No module named 'app'）
        cmd=(env PYTHONPATH="$PROJECT_DIR" conda run -n "$CONDA_ENV" "$PYTHON_BIN" "$VLLM_PYTHON_LAUNCHER" --stop --force)
    else
        cmd=(env PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" "$VLLM_PYTHON_LAUNCHER" --stop --force)
    fi
    if "${cmd[@]}" >/dev/null 2>&1; then
        echo -e "${GREEN}已请求Python管理器停止vLLM${NC}"
        sleep 2
        return 0
    fi
    return 1
}

# 通过Python管理器停止sglang
stop_sglang_python() {
    if [ ! -f "$SGLANG_PYTHON_LAUNCHER" ]; then
        return 1
    fi
    local -a cmd
    if [ "$CONDA_AVAILABLE" = true ]; then
        # 确保无论从哪里执行，都能 import app（避免 ModuleNotFoundError: No module named 'app'）
        cmd=(env PYTHONPATH="$PROJECT_DIR" conda run -n "$CONDA_ENV" "$PYTHON_BIN" "$SGLANG_PYTHON_LAUNCHER" --stop --force)
    else
        cmd=(env PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" "$SGLANG_PYTHON_LAUNCHER" --stop --force)
    fi
    if "${cmd[@]}" >/dev/null 2>&1; then
        echo -e "${GREEN}已请求Python管理器停止sglang${NC}"
        sleep 2
        return 0
    fi
    return 1
}

# 停止vLLM（包括通过进程名查找）
stop_vllm_all() {
    echo -e "${GREEN}查找并停止所有vLLM相关进程...${NC}"
    
    # 通过进程名查找
    if [ "$IS_WINDOWS" = true ]; then
        # Windows: 使用 tasklist
        if command -v tasklist &> /dev/null; then
            local pids=$(tasklist //FI "IMAGENAME eq python.exe" //FO CSV 2>/dev/null | grep -i "vllm" | awk -F',' '{print $2}' | tr -d '"' || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止vLLM进程 (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止vLLM进程 (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    else
        # Linux/Mac: 使用 pgrep
        if command -v pgrep &> /dev/null; then
            local pids=$(pgrep -f "vllm.*api_server\|python.*vllm" 2>/dev/null || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止vLLM进程 (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止vLLM进程 (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    fi
    
    # 通过端口查找（备用方案）
    local port_pid=$(find_process_by_port $VLLM_PORT)
    if [ -n "$port_pid" ] && is_process_running $port_pid; then
        echo -e "${GREEN}通过端口 $VLLM_PORT 找到vLLM进程 (PID: $port_pid)，正在停止...${NC}"
        kill_process $port_pid false
        sleep 2
        if is_process_running $port_pid; then
            kill_process $port_pid true
        fi
    fi
}

# 停止sglang（包括通过进程名查找）
stop_sglang_all() {
    echo -e "${GREEN}查找并停止所有sglang相关进程...${NC}"
    
    # 通过进程名查找
    if [ "$IS_WINDOWS" = true ]; then
        # Windows: 使用 tasklist
        if command -v tasklist &> /dev/null; then
            local pids=$(tasklist //FI "IMAGENAME eq python.exe" //FO CSV 2>/dev/null | grep -i "sglang" | awk -F',' '{print $2}' | tr -d '"' || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止sglang进程 (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止sglang进程 (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    else
        # Linux/Mac: 使用 pgrep
        if command -v pgrep &> /dev/null; then
            local pids=$(pgrep -f "sglang\|python.*sglang" 2>/dev/null || true)
            if [ -n "$pids" ]; then
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${GREEN}停止sglang进程 (PID: $pid)...${NC}"
                        kill_process $pid false
                    fi
                done
                sleep 2
                # 强制停止仍在运行的进程
                for pid in $pids; do
                    if is_process_running $pid; then
                        echo -e "${YELLOW}强制停止sglang进程 (PID: $pid)...${NC}"
                        kill_process $pid true
                    fi
                done
            fi
        fi
    fi
    
    # 通过端口查找（备用方案）
    local port_pid=$(find_process_by_port $SGLANG_PORT)
    if [ -n "$port_pid" ] && is_process_running $port_pid; then
        echo -e "${GREEN}通过端口 $SGLANG_PORT 找到sglang进程 (PID: $port_pid)，正在停止...${NC}"
        kill_process $port_pid false
        sleep 2
        if is_process_running $port_pid; then
            kill_process $port_pid true
        fi
    fi
}

# 停止Nginx
stop_nginx_all() {
    local NGINX_CONF="$PROJECT_DIR/nginx/nginx.conf"
    
    # 先尝试通过PID文件停止
    stop_service "nginx"
    
    # 如果PID文件方式失败，尝试通过进程查找
    if [ -f "$NGINX_CONF" ]; then
        # 查找使用我们配置文件的nginx主进程
        local master_pid=""
        if command -v ps &> /dev/null; then
            master_pid=$(ps aux | grep "nginx: master process.*$NGINX_CONF" | grep -v grep | awk '{print $2}' | head -n 1)
        fi
        
        if [ -z "$master_pid" ]; then
            # 如果找不到，尝试通过端口8000查找
            master_pid=$(find_process_by_port 8000)
        fi
        
        if [ -n "$master_pid" ] && is_process_running $master_pid; then
            echo -e "${GREEN}停止Nginx主进程 (PID: $master_pid)...${NC}"
            # 优雅停止（Windows不支持QUIT信号）
            if [ "$IS_WINDOWS" != true ]; then
                kill -QUIT $master_pid 2>/dev/null || kill_process $master_pid false
            else
                kill_process $master_pid false
            fi
            sleep 2
            
            # 如果还在运行，强制停止
            if is_process_running $master_pid; then
                echo -e "${YELLOW}强制停止Nginx...${NC}"
                kill_process $master_pid true
            fi
            
            # 停止所有worker进程
            if [ "$IS_WINDOWS" != true ] && command -v ps &> /dev/null; then
                local worker_pids=$(ps aux | grep "nginx: worker process" | grep -v grep | awk '{print $2}')
                if [ -n "$worker_pids" ]; then
                    for pid in $worker_pids; do
                        if is_process_running $pid; then
                            kill_process $pid false
                        fi
                    done
                fi
            fi
            
            echo -e "${GREEN}Nginx已停止${NC}"
        else
            echo -e "${YELLOW}未找到运行中的Nginx进程${NC}"
        fi
    fi
}

# 主流程
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  vLLM Proxy 停止脚本${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    # 1. 先停止FastAPI（包括所有worker进程）
    echo -e "${YELLOW}[1/5] 停止FastAPI服务...${NC}"
    stop_service "fastapi"
    stop_fastapi_workers
    
    # 2. 停止vLLM（优先使用Python管理器）
    echo -e "${YELLOW}[2/5] 停止vLLM服务...${NC}"
    stop_vllm_python || true
    stop_service "vllm"
    stop_vllm_all
    
    # 3. 停止sglang（优先使用Python管理器）
    echo -e "${YELLOW}[3/5] 停止sglang服务...${NC}"
    stop_sglang_python || true
    stop_service "sglang"
    stop_sglang_all
    
    # 4. 停止Nginx（可选）
    echo -e "${YELLOW}[4/5] 检查Nginx服务...${NC}"
    if command -v nginx &> /dev/null; then
        read -p "是否停止Nginx? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            stop_nginx_all
        else
            echo -e "${YELLOW}跳过Nginx停止${NC}"
        fi
    else
        echo -e "${YELLOW}未安装Nginx，跳过${NC}"
    fi
    
    # 5. 清理残留的PID文件
    echo -e "${YELLOW}[5/5] 清理残留文件...${NC}"
    if [ -d "$PID_DIR" ]; then
        for pid_file in "$PID_DIR"/*.pid; do
            if [ -f "$pid_file" ]; then
                local pid=$(cat "$pid_file" 2>/dev/null || echo "")
                if [ -n "$pid" ] && ! is_process_running $pid; then
                    rm -f "$pid_file"
                    echo -e "${GREEN}清理残留PID文件: $(basename $pid_file)${NC}"
                fi
            fi
        done
    fi
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  停止完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "提示：如果仍有服务在运行，请检查："
    echo "  - 日志文件: $PROJECT_DIR/logs/"
    echo "  - PID文件: $PID_DIR/"
    echo "  - 端口占用: lsof -i :$FASTAPI_PORT -i :$VLLM_PORT -i :$SGLANG_PORT"
}

main

