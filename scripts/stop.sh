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

# 停止服务函数
stop_service() {
    local service_name=$1
    local pid_file="$PID_DIR/$service_name.pid"
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "${GREEN}停止 $service_name (PID: $pid)...${NC}"
            kill $pid
            sleep 2
            
            # 如果还在运行，强制杀死
            if ps -p $pid > /dev/null 2>&1; then
                echo -e "${YELLOW}强制停止 $service_name...${NC}"
                kill -9 $pid
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

# 主流程
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  vLLM Proxy 停止脚本${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    stop_service "fastapi"
    stop_service "vllm"
    pkill -f VLLM
    
    # 停止Nginx（可选）
    if command -v nginx &> /dev/null; then
        read -p "是否停止Nginx? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            stop_service "nginx"
            
            # 如果PID文件方式失败，尝试通过进程查找
            NGINX_CONF="$PROJECT_DIR/nginx/nginx.conf"
            if [ -f "$NGINX_CONF" ]; then
                # 查找使用我们配置文件的nginx主进程
                local master_pid=$(ps aux | grep "nginx: master process.*$NGINX_CONF" | grep -v grep | awk '{print $2}' | head -n 1)
                
                if [ -z "$master_pid" ]; then
                    # 如果找不到，尝试通过端口8000查找
                    master_pid=$(lsof -ti :8000 | head -n 1)
                fi
                
                if [ -n "$master_pid" ] && ps -p $master_pid > /dev/null 2>&1; then
                    echo -e "${GREEN}停止Nginx主进程 (PID: $master_pid)...${NC}"
                    # 优雅停止
                    kill -QUIT $master_pid 2>/dev/null || kill $master_pid 2>/dev/null || true
                    sleep 2
                    
                    # 如果还在运行，强制停止
                    if ps -p $master_pid > /dev/null 2>&1; then
                        echo -e "${YELLOW}强制停止Nginx...${NC}"
                        kill -9 $master_pid 2>/dev/null || true
                    fi
                    
                    # 停止所有worker进程
                    local worker_pids=$(ps aux | grep "nginx: worker process" | grep -v grep | awk '{print $2}')
                    if [ -n "$worker_pids" ]; then
                        for pid in $worker_pids; do
                            kill $pid 2>/dev/null || true
                        done
                    fi
                    
                    echo -e "${GREEN}Nginx已停止${NC}"
                else
                    echo -e "${YELLOW}未找到运行中的Nginx进程${NC}"
                fi
            fi
        fi
    fi
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  停止完成${NC}"
    echo -e "${GREEN}========================================${NC}"
}

main

