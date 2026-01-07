"""日志管理模块 - 处理日志轮转和清理"""
import os
import glob
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from app.monitoring import logger


def get_log_files(log_dir: str) -> List[str]:
    """获取所有日志文件"""
    log_path = Path(log_dir)
    if not log_path.exists():
        return []
    
    # 查找所有日志文件（包括轮转后的文件）
    log_files = []
    for pattern in ["*.log", "*.log.*"]:
        log_files.extend(glob.glob(str(log_path / pattern)))
    
    return sorted(log_files, key=os.path.getmtime, reverse=True)


def rotate_log_file(log_file: str, max_size_mb: float = 100.0) -> bool:
    """
    轮转日志文件（如果超过大小限制）
    
    Args:
        log_file: 日志文件路径
        max_size_mb: 最大文件大小（MB）
    
    Returns:
        是否进行了轮转
    """
    if not os.path.exists(log_file):
        return False
    
    max_size_bytes = max_size_mb * 1024 * 1024
    file_size = os.path.getsize(log_file)
    
    if file_size < max_size_bytes:
        return False
    
    # 生成带时间戳的新文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rotated_file = f"{log_file}.{timestamp}"
    
    try:
        # 重命名当前日志文件
        os.rename(log_file, rotated_file)
        logger.info(
            "log_rotated",
            log_file=log_file,
            rotated_file=rotated_file,
            size_mb=file_size / (1024 * 1024)
        )
        return True
    except Exception as e:
        logger.error("log_rotate_failed", log_file=log_file, error=str(e))
        return False


def clean_old_logs(log_dir: str, days_to_keep: int = 7) -> Dict[str, int]:
    """
    清理旧日志文件
    
    Args:
        log_dir: 日志目录
        days_to_keep: 保留天数
    
    Returns:
        清理统计信息
    """
    cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
    deleted_count = 0
    freed_bytes = 0
    
    log_files = get_log_files(log_dir)
    
    for log_file in log_files:
        try:
            # 只删除轮转后的日志文件（带时间戳的），不删除当前正在使用的日志文件
            if os.path.basename(log_file).count('.') > 1:  # 轮转后的文件格式: name.log.timestamp
                file_mtime = os.path.getmtime(log_file)
                if file_mtime < cutoff_time:
                    file_size = os.path.getsize(log_file)
                    os.remove(log_file)
                    deleted_count += 1
                    freed_bytes += file_size
                    logger.debug(
                        "log_deleted",
                        log_file=log_file,
                        age_days=(time.time() - file_mtime) / (24 * 60 * 60)
                    )
        except Exception as e:
            logger.error("log_delete_failed", log_file=log_file, error=str(e))
    
    return {
        "deleted_files": deleted_count,
        "freed_space_mb": freed_bytes / (1024 * 1024)
    }


def get_log_stats(log_dir: str) -> Dict:
    """获取日志统计信息"""
    log_files = get_log_files(log_dir)
    total_size = 0
    file_count = 0
    oldest_file = None
    newest_file = None
    
    for log_file in log_files:
        if os.path.exists(log_file):
            file_size = os.path.getsize(log_file)
            total_size += file_size
            file_count += 1
            
            file_mtime = os.path.getmtime(log_file)
            file_time = datetime.fromtimestamp(file_mtime)
            
            if oldest_file is None or file_mtime < oldest_file[1]:
                oldest_file = (log_file, file_mtime, file_time)
            if newest_file is None or file_mtime > newest_file[1]:
                newest_file = (log_file, file_mtime, file_time)
    
    return {
        "total_files": file_count,
        "total_size_mb": total_size / (1024 * 1024),
        "oldest_file": {
            "path": oldest_file[0] if oldest_file else None,
            "modified": oldest_file[2].isoformat() if oldest_file else None
        },
        "newest_file": {
            "path": newest_file[0] if newest_file else None,
            "modified": newest_file[2].isoformat() if newest_file else None
        }
    }


def setup_log_rotation(
    log_dir: str,
    max_size_mb: float = 100.0,
    days_to_keep: int = 7,
    check_interval_hours: int = 24
):
    """
    设置日志轮转和清理任务
    
    Args:
        log_dir: 日志目录
        max_size_mb: 单个日志文件最大大小（MB）
        days_to_keep: 保留天数
        check_interval_hours: 检查间隔（小时）
    """
    import asyncio
    
    async def rotation_task():
        """后台日志轮转任务"""
        while True:
            try:
                # 轮转大文件
                log_files = [f for f in get_log_files(log_dir) if f.endswith('.log') and not '.' in os.path.basename(f)[:-4]]
                for log_file in log_files:
                    rotate_log_file(log_file, max_size_mb)
                
                # 清理旧日志
                cleanup_result = clean_old_logs(log_dir, days_to_keep)
                if cleanup_result["deleted_files"] > 0:
                    logger.info(
                        "log_cleanup_completed",
                        deleted_files=cleanup_result["deleted_files"],
                        freed_space_mb=cleanup_result["freed_space_mb"]
                    )
                
                # 等待指定时间后再次检查
                await asyncio.sleep(check_interval_hours * 3600)
            except asyncio.CancelledError:
                logger.info("log_rotation_task_cancelled")
                break
            except Exception as e:
                logger.error("log_rotation_task_error", error=str(e))
                await asyncio.sleep(3600)  # 出错后等待1小时再试
    
    return rotation_task

