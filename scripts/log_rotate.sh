#!/bin/bash

# 日志轮转和清理脚本
# 使用方法: ./scripts/log_rotate.sh [log_dir] [max_size_mb] [days_to_keep]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# 默认参数
LOG_DIR="${1:-$PROJECT_DIR/logs}"
MAX_SIZE_MB="${2:-100}"
DAYS_TO_KEEP="${3:-7}"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}开始日志轮转和清理...${NC}"
echo "日志目录: $LOG_DIR"
echo "最大文件大小: ${MAX_SIZE_MB}MB"
echo "保留天数: ${DAYS_TO_KEEP}天"
echo ""

# 轮转大文件
rotate_log() {
    local log_file=$1
    local max_size_bytes=$(($MAX_SIZE_MB * 1024 * 1024))
    
    if [ ! -f "$log_file" ]; then
        return 0
    fi
    
    local file_size=$(stat -f%z "$log_file" 2>/dev/null || stat -c%s "$log_file" 2>/dev/null || echo 0)
    
    if [ "$file_size" -lt "$max_size_bytes" ]; then
        return 0
    fi
    
    # 生成带时间戳的新文件名
    local timestamp=$(date +"%Y%m%d_%H%M%S")
    local rotated_file="${log_file}.${timestamp}"
    
    # 重命名当前日志文件
    mv "$log_file" "$rotated_file" 2>/dev/null || return 1
    
    echo -e "${GREEN}轮转日志文件: $log_file -> $rotated_file (${file_size} bytes)${NC}"
    
    # 创建新的空日志文件（保持文件描述符有效）
    touch "$log_file"
    
    return 0
}

# 清理旧日志
clean_old_logs() {
    local cutoff_time=$(date -d "${DAYS_TO_KEEP} days ago" +%s 2>/dev/null || \
                       date -v-${DAYS_TO_KEEP}d +%s 2>/dev/null || \
                       echo $(($(date +%s) - ${DAYS_TO_KEEP} * 86400)))
    
    local deleted_count=0
    local freed_bytes=0
    
    # 查找所有轮转后的日志文件（格式: *.log.timestamp）
    for log_file in "$LOG_DIR"/*.log.*; do
        if [ -f "$log_file" ]; then
            local file_mtime=$(stat -f%Y "$log_file" 2>/dev/null || \
                              stat -c%Y "$log_file" 2>/dev/null || \
                              echo 0)
            
            if [ "$file_mtime" -lt "$cutoff_time" ]; then
                local file_size=$(stat -f%z "$log_file" 2>/dev/null || \
                                 stat -c%s "$log_file" 2>/dev/null || \
                                 echo 0)
                
                rm -f "$log_file"
                deleted_count=$((deleted_count + 1))
                freed_bytes=$((freed_bytes + file_size))
                
                echo -e "${YELLOW}删除旧日志: $log_file${NC}"
            fi
        fi
    done
    
    if [ "$deleted_count" -gt 0 ]; then
        local freed_mb=$((freed_bytes / 1024 / 1024))
        echo -e "${GREEN}清理完成: 删除 $deleted_count 个文件，释放 ${freed_mb}MB 空间${NC}"
    else
        echo -e "${GREEN}没有需要清理的旧日志${NC}"
    fi
}

# 主流程
main() {
    # 确保日志目录存在
    mkdir -p "$LOG_DIR"
    
    # 轮转大文件
    echo "检查需要轮转的日志文件..."
    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            # 只轮转当前日志文件（不包含时间戳的）
            if [[ ! "$log_file" =~ \.[0-9]{8}_[0-9]{6}$ ]]; then
                rotate_log "$log_file"
            fi
        fi
    done
    
    echo ""
    
    # 清理旧日志
    echo "清理 ${DAYS_TO_KEEP} 天前的日志文件..."
    clean_old_logs
    
    echo ""
    echo -e "${GREEN}日志轮转和清理完成${NC}"
}

main

