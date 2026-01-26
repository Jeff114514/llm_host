"""vLLM 进程管理器：通过 Python 启动与停止 vLLM 服务。"""

from __future__ import annotations

import os
import shutil
import shlex
import signal
import subprocess
import sys
import time
import json
import multiprocessing
import threading
import fcntl
from typing import Dict, IO, List, Optional

import httpx

from app.log_manager import rotate_log_file
from app.monitoring import logger
from app.models import AppConfig, VLLMLaunchMode


class VLLMManager:
    """负责启动、健康检测与停止 vLLM 进程的管理器。"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.pid_file = config.vllm.pid_file
        self.log_file = config.vllm.log_file
        self._process: Optional[subprocess.Popen] = None
        self._api_process: Optional[multiprocessing.Process] = None
        self._log_fp: Optional[IO[bytes]] = None

        # 确保必要的目录存在
        os.makedirs(self.config.vllm.pid_dir, exist_ok=True)
        log_dir = os.path.dirname(self.log_file) or "."
        os.makedirs(log_dir, exist_ok=True)

    # ---------------------- 内部工具方法 ---------------------- #
    def _read_pid(self) -> Optional[int]:
        try:
            with open(self.pid_file, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _write_pid(self, pid: int) -> None:
        with open(self.pid_file, "w", encoding="utf-8") as f:
            f.write(str(pid))

    def _remove_pid(self) -> None:
        try:
            os.remove(self.pid_file)
        except FileNotFoundError:
            pass

    def _load_start_command(self, override_command: Optional[str]) -> str:
        if override_command:
            return override_command.strip()
        if self.config.vllm.start_cmd:
            return self.config.vllm.start_cmd.strip()
        if os.path.exists(self.config.vllm.start_cmd_file):
            with open(self.config.vllm.start_cmd_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        raise RuntimeError("未找到 vLLM 启动命令，请设置 config.vllm.start_cmd 或配置文件。")

    def _parse_env_file(self) -> Dict[str, str]:
        """解析可选的环境变量文件（KEY=VALUE，每行一条）。"""
        env_file = self.config.vllm.python_launcher.env_file
        results: Dict[str, str] = {}
        if env_file and os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    results[key.strip()] = value.strip()
        return results

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.update(self._parse_env_file())
        extra_env = self.config.vllm.extra_env or {}
        if extra_env:
            logger.debug("vllm_extra_env_loaded", extra_env_keys=list(extra_env.keys()))
        env.update(extra_env)
        return env

    def _prepare_lora_env(self, env: Dict[str, str]) -> Dict[str, str]:
        """根据 LoRA 配置注入必要环境变量。"""
        lora_cfg = self.config.vllm.lora
        
        # 如果 LoRA 未启用，直接返回
        if not lora_cfg.enabled:
            return env
        
        if lora_cfg.runtime_resolver.allow_runtime_updates:
            env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "true"
        
        # 检查是否启用了 filesystem resolver 插件
        plugins_list = []
        if lora_cfg.runtime_resolver.plugins:
            plugins_list = [p.strip().lower() for p in lora_cfg.runtime_resolver.plugins]
            env["VLLM_PLUGINS"] = ",".join(lora_cfg.runtime_resolver.plugins)
        
        # 如果启用了 lora_filesystem_resolver 插件，或者 VLLM_PLUGINS 未设置（会加载所有插件），
        # 必须设置 VLLM_LORA_RESOLVER_CACHE_DIR
        needs_cache_dir = (
            "lora_filesystem_resolver" in plugins_list 
            or "filesystem_resolver" in plugins_list
            or (not lora_cfg.runtime_resolver.plugins and lora_cfg.runtime_resolver.allow_runtime_updates)
            or lora_cfg.runtime_resolver.allow_runtime_updates  # 防御性：总是设置，以防插件被自动加载
        )
        
        if needs_cache_dir or lora_cfg.runtime_resolver.cache_dir:
            # 使用配置的 cache_dir，如果未配置则使用默认值
            cache_dir = lora_cfg.runtime_resolver.cache_dir or "./lora_cache"
            
            # 将相对路径转换为绝对路径（基于项目根目录）
            from pathlib import Path
            if not os.path.isabs(cache_dir):
                # 尝试从配置文件路径推断项目根目录
                config_file = os.getenv("CONFIG_FILE", "config/config.yaml")
                if os.path.isabs(config_file):
                    # 配置文件是绝对路径，使用其父目录的父目录作为项目根
                    project_root = Path(config_file).parent.parent.resolve()
                elif os.path.exists(config_file):
                    # 配置文件是相对路径且存在，使用其父目录的父目录作为项目根
                    project_root = Path(config_file).resolve().parent.parent
                else:
                    # 配置文件不存在，使用当前工作目录
                    project_root = Path.cwd().resolve()
                cache_dir = str(project_root / cache_dir)
            
            # 确保使用绝对路径
            cache_path = Path(cache_dir).resolve()
            cache_dir_abs = str(cache_path)
            
            # 确保目录存在
            try:
                cache_path.mkdir(parents=True, exist_ok=True)
                if not cache_path.is_dir():
                    raise ValueError(f"缓存路径不是有效目录: {cache_dir_abs}")
                # 验证目录可访问
                if not os.access(cache_path, os.R_OK | os.W_OK):
                    raise ValueError(f"缓存目录不可访问: {cache_dir_abs}")
                # 再次验证目录确实存在（防御性检查）
                if not os.path.exists(cache_dir_abs):
                    raise ValueError(f"缓存目录创建后仍不存在: {cache_dir_abs}")
            except (OSError, ValueError) as exc:
                logger.error(
                    "lora_cache_dir_creation_failed",
                    cache_dir=cache_dir_abs,
                    error=str(exc),
                    cwd=os.getcwd(),
                )
                raise RuntimeError(
                    f"无法创建或访问 LoRA 缓存目录 '{cache_dir_abs}': {exc}"
                ) from exc
            
            # 使用绝对路径设置环境变量
            env["VLLM_LORA_RESOLVER_CACHE_DIR"] = cache_dir_abs
            logger.info(
                "lora_cache_dir_set",
                cache_dir=cache_dir_abs,
                plugins=",".join(plugins_list) if plugins_list else "none",
            )
        
        return env

    def _build_lora_cli_args(self) -> List[str]:
        """将 LoRA 相关配置转换为 vLLM CLI 参数列表。"""
        lora_cfg = self.config.vllm.lora
        if not lora_cfg.enabled:
            return []

        args: List[str] = ["--enable-lora"]

        if lora_cfg.max_lora_rank is not None:
            args += ["--max-lora-rank", str(lora_cfg.max_lora_rank)]
        if lora_cfg.max_loras is not None:
            args += ["--max-loras", str(lora_cfg.max_loras)]
        if lora_cfg.max_cpu_loras is not None:
            # 确保 max_cpu_loras >= max_loras（vLLM 的要求）
            max_cpu_loras = lora_cfg.max_cpu_loras
            if lora_cfg.max_loras is not None and max_cpu_loras < lora_cfg.max_loras:
                logger.warning(
                    "max_cpu_loras_adjusted",
                    original=max_cpu_loras,
                    adjusted=lora_cfg.max_loras,
                    reason="max_cpu_loras must be >= max_loras",
                )
                max_cpu_loras = lora_cfg.max_loras
            args += ["--max-cpu-loras", str(max_cpu_loras)]

        for module in lora_cfg.preload:
            payload: Dict[str, str] = {"name": module.name, "path": module.path}
            if module.base_model_name:
                payload["base_model_name"] = module.base_model_name
                args += ["--lora-modules", json.dumps(payload)]
            else:
                args += ["--lora-modules", f"{module.name}={module.path}"]

        if lora_cfg.default_mm_loras:
            args += ["--default-mm-loras", json.dumps(lora_cfg.default_mm_loras)]
        if lora_cfg.limit_mm_per_prompt:
            args += ["--limit-mm-per-prompt", json.dumps(lora_cfg.limit_mm_per_prompt)]

        return args

    def _get_python_prefix(self) -> List[str]:
        """获取用于启动 vLLM 的 Python 前缀命令。"""
        launcher = self.config.vllm.python_launcher
        if launcher.conda_env:
            if shutil.which("conda"):
                return ["conda", "run", "-n", launcher.conda_env, "python"]
            logger.warning(
                "conda_not_found_fallback",
                conda_env=launcher.conda_env,
                fallback=sys.executable,
            )
        return [sys.executable]

    def _extract_vllm_args(self, command: str) -> List[str]:
        """
        规范化启动命令，提取传递给 vLLM 模块的参数。
        会剔除前置的 python/-m/module 等包装，只保留参数部分。
        """
        tokens = shlex.split(command)
        if not tokens:
            return []

        cleaned: List[str] = []
        skip_module = False
        python_tokens = {
            "python",
            "python3",
            "python.exe",
            sys.executable,
        }
        module_tokens = {
            "vllm",
            "vllm.entrypoints.openai.api_server",
            "vllm.entrypoints.api_server",
            "vllm.entrypoints.openai.cli",
            "vllm.entrypoints.openai.cli:serve",
        }

        for tok in tokens:
            if tok in python_tokens:
                continue
            if tok == "-m":
                skip_module = True
                continue
            if skip_module:
                skip_module = False
                continue
            if tok in module_tokens:
                continue
            cleaned.append(tok)
        return cleaned

    def _ensure_log_handle(self) -> IO[bytes]:
        # 先做简单的日志轮转
        rotate_log_file(self.log_file, self.config.vllm.log_max_size_mb)
        if self._log_fp and not self._log_fp.closed:
            return self._log_fp
        self._log_fp = open(self.log_file, "ab", buffering=0)
        return self._log_fp

    def _is_pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    # ---------------------- 对外方法 ---------------------- #
    def is_running(self) -> bool:
        if self._process and self._process.poll() is None:
            return True
        if self._api_process and self._api_process.is_alive():
            return True
        pid = self._read_pid()
        if pid is None:
            return False
        alive = self._is_pid_running(pid)
        if not alive:
            self._remove_pid()
        return alive

    def start(self, override_command: Optional[str] = None) -> int:
        """启动 vLLM 进程，返回 PID。使用文件锁防止并发启动。"""
        # 第一次检查：快速检查是否已运行
        if self.is_running():
            existing_pid = self._read_pid()
            if existing_pid:
                logger.info("vllm_already_running", pid=existing_pid)
                return existing_pid

        # 使用 PID 文件作为锁文件，防止并发启动
        lock_file_path = self.pid_file + ".lock"
        os.makedirs(os.path.dirname(lock_file_path) or ".", exist_ok=True)
        
        lock_file = None
        try:
            # 创建锁文件
            lock_file = open(lock_file_path, "w")
            try:
                # 尝试获取排他锁（非阻塞）
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # 如果无法获取锁，说明另一个进程正在启动
                lock_file.close()
                lock_file = None
                # 等待一小段时间，然后再次检查
                time.sleep(0.5)
                if self.is_running():
                    existing_pid = self._read_pid()
                    if existing_pid:
                        logger.info("vllm_started_by_another_process", pid=existing_pid)
                        return existing_pid
                # 如果仍然无法获取锁，抛出异常
                raise RuntimeError("无法获取启动锁，可能有另一个进程正在启动 vLLM")
            
            # 获取锁后，再次检查（双重检查锁定模式）
            try:
                if self.is_running():
                    existing_pid = self._read_pid()
                    if existing_pid:
                        logger.info("vllm_already_running_after_lock", pid=existing_pid)
                        return existing_pid
                
                # 现在可以安全地启动
                command = self._load_start_command(override_command)
                python_mode = self.config.vllm.python_launcher.enabled
                launch_mode = self.config.vllm.launch_mode

                # 确保日志文件存在并可写
                log_dir = os.path.dirname(self.log_file) or "."
                os.makedirs(log_dir, exist_ok=True)
                
                env = self._prepare_lora_env(self._build_env())
                lora_args = self._build_lora_cli_args()

                if launch_mode == VLLMLaunchMode.PYTHON_API:
                    # 使用 Python API 方式启动
                    # 使用程序捕获输出并写入日志文件，而不是直接重定向
                    vllm_args = self._extract_vllm_args(command) + lora_args
                    
                    # 直接使用 subprocess 运行 vLLM 模块
                    python_cmd = self._get_python_prefix()
                    launch_cmd = python_cmd + [
                        "-m",
                        "vllm.entrypoints.openai.api_server",
                        *vllm_args,
                    ]
                    
                    # 写入启动信息到日志文件
                    try:
                        import datetime
                        with open(self.log_file, "a", encoding="utf-8") as f:
                            f.write(f"\n{'='*80}\n")
                            f.write(f"[{datetime.datetime.now().isoformat()}] 启动 vLLM 服务\n")
                            f.write(f"[{datetime.datetime.now().isoformat()}] 命令: {' '.join(launch_cmd)}\n")
                            f.write(f"[{datetime.datetime.now().isoformat()}] 日志文件: {self.log_file}\n")
                            f.write(f"{'='*80}\n")
                    except Exception:
                        pass  # 忽略写入错误，继续执行
                    
                    try:
                        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        # 设置 PYTHONUNBUFFERED 环境变量以确保输出不被缓冲
                        env["PYTHONUNBUFFERED"] = "1"
                        
                        # 记录环境变量信息（特别是 extra_env）
                        extra_env_keys = list(self.config.vllm.extra_env.keys()) if self.config.vllm.extra_env else []
                        if extra_env_keys:
                            logger.info(
                                "vllm_starting_with_extra_env",
                                extra_env_keys=extra_env_keys,
                                extra_env_values={k: env.get(k, "***") for k in extra_env_keys}
                            )
                        
                        # 使用管道捕获输出，而不是直接重定向到文件
                        self._process = subprocess.Popen(
                            launch_cmd,
                            stdout=subprocess.PIPE,  # 使用管道捕获 stdout
                            stderr=subprocess.STDOUT,  # 将 stderr 合并到 stdout
                            env=env,
                            creationflags=creation_flags,
                            bufsize=1,  # 行缓冲
                            text=True,  # 文本模式
                            encoding="utf-8",
                            errors="replace",  # 遇到编码错误时替换
                        )
                        pid = self._process.pid
                        self._write_pid(pid)
                        
                        # 启动线程来读取输出并写入日志文件
                        def log_writer():
                            """在后台线程中读取进程输出并写入日志文件"""
                            try:
                                with open(self.log_file, "a", encoding="utf-8", buffering=1) as log_file:
                                    if self._process.stdout:
                                        for line in iter(self._process.stdout.readline, ''):
                                            if line:
                                                log_file.write(line)
                                                log_file.flush()  # 立即刷新到磁盘
                            except Exception as exc:
                                logger.error("log_writer_error", error=str(exc))
                        
                        log_thread = threading.Thread(target=log_writer, daemon=True)
                        log_thread.start()
                        
                        # 写入启动成功信息
                        try:
                            import datetime
                            with open(self.log_file, "a", encoding="utf-8") as f:
                                f.write(f"[{datetime.datetime.now().isoformat()}] vLLM 进程已启动，PID: {pid}\n")
                        except Exception:
                            pass
                        
                        # 等待一段时间检查进程是否成功启动
                        # 给进程一些时间来初始化，如果在这段时间内退出，说明启动失败
                        startup_check_delay = 10  # 等待 10 秒
                        for _ in range(startup_check_delay):
                            if self._process.poll() is not None:
                                # 进程已经退出，启动失败
                                exit_code = self._process.returncode
                                self._remove_pid()
                                
                                # 尝试读取最后的错误日志
                                error_summary = ""
                                common_errors = []
                                try:
                                    if os.path.exists(self.log_file):
                                        with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                                            lines = f.readlines()
                                            # 获取最后 100 行进行分析
                                            recent_lines = lines[-100:]
                                            
                                            # 检查常见错误模式
                                            for line in recent_lines:
                                                line_upper = line.upper()
                                                if "marlin_gemm" in line or (("operator" in line) and ("does not exist" in line)):
                                                    common_errors.append("检测到自定义操作符错误：vLLM 的自定义操作符可能未正确编译。建议重新安装 vLLM。")
                                                elif "CUDA" in line_upper and ("ERROR" in line_upper or "FAILED" in line_upper):
                                                    common_errors.append("检测到 CUDA 相关错误：请检查 CUDA 驱动和 PyTorch 版本兼容性。")
                                                elif "OUT OF MEMORY" in line_upper or "OOM" in line_upper:
                                                    common_errors.append("检测到内存不足错误：请减少 GPU 内存使用或使用更小的模型。")
                                                elif "MODEL" in line_upper and ("NOT FOUND" in line_upper or "CANNOT FIND" in line_upper):
                                                    common_errors.append("检测到模型路径错误：请检查模型路径是否正确。")
                                            
                                            # 获取最后 50 行作为错误摘要
                                            error_lines = [line for line in recent_lines if "ERROR" in line.upper() or "Traceback" in line or "RuntimeError" in line or "Exception" in line or "ValidationError" in line]
                                            if error_lines:
                                                error_summary = "\n".join(error_lines[-15:])  # 最后 15 个错误行
                                except Exception:
                                    pass
                                
                                error_msg = f"vLLM 进程在启动后 {startup_check_delay} 秒内退出，退出码: {exit_code}"
                                
                                # 添加常见错误提示
                                if common_errors:
                                    error_msg += "\n\n可能的解决方案："
                                    for i, hint in enumerate(set(common_errors), 1):  # 使用 set 去重
                                        error_msg += f"\n{i}. {hint}"
                                
                                if error_summary:
                                    error_msg += f"\n\n错误详情:\n{error_summary}"
                                
                                try:
                                    import datetime
                                    with open(self.log_file, "a", encoding="utf-8") as f:
                                        f.write(f"[{datetime.datetime.now().isoformat()}] [ERROR] {error_msg}\n")
                                except Exception:
                                    pass
                                
                                logger.error(
                                    "vllm_startup_failed",
                                    pid=pid,
                                    exit_code=exit_code,
                                    error_summary=error_summary[:500] if error_summary else None,
                                )
                                raise RuntimeError(error_msg)
                            time.sleep(1)
                        
                        logger.info(
                            "vllm_started_python_api",
                            pid=pid,
                            command=" ".join(launch_cmd),
                            log_file=self.log_file,
                        )
                        return pid
                    except Exception as exc:  # noqa: BLE001
                        try:
                            import datetime
                            with open(self.log_file, "a", encoding="utf-8") as f:
                                f.write(f"[{datetime.datetime.now().isoformat()}] [ERROR] 启动 vLLM 失败: {exc}\n")
                        except Exception:
                            pass
                        logger.error("vllm_start_failed", error=str(exc), command=command)
                        raise
                else:
                    # 回退：命令行/子进程方式
                    log_fp = self._ensure_log_handle()
                    if python_mode:
                        vllm_args = self._extract_vllm_args(command) + lora_args
                        launch_cmd = self._get_python_prefix() + [
                            "-m",
                            "vllm.entrypoints.openai.api_server",
                            *vllm_args,
                        ]
                    else:
                        launch_cmd = shlex.split(command) + lora_args

                    try:
                        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        self._process = subprocess.Popen(
                            launch_cmd,
                            stdout=log_fp,
                            stderr=log_fp,
                            env=env,
                            creationflags=creation_flags,
                        )
                        pid = self._process.pid
                        self._write_pid(pid)
                        logger.info(
                            "vllm_started_subprocess",
                            pid=pid,
                            command=" ".join(launch_cmd),
                            python_launcher=python_mode,
                        )
                        return pid
                    except (OSError, subprocess.SubprocessError) as exc:
                        logger.error("vllm_start_failed", error=str(exc), command=command)
                        raise
            finally:
                # 释放文件锁
                if lock_file:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except (IOError, OSError):
                        pass
                    lock_file.close()
                    try:
                        os.remove(lock_file_path)
                    except OSError:
                        pass
        except Exception as exc:
            # 如果启动过程中出现异常，确保释放锁
            if lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except (IOError, OSError):
                    pass
                try:
                    lock_file.close()
                except Exception:
                    pass
                try:
                    os.remove(lock_file_path)
                except OSError:
                    pass
            raise

    def wait_for_ready(self, host: str, port: int, timeout: int = 60) -> bool:
        """等待 vLLM /health 或 /v1/models 就绪。"""
        deadline = time.time() + timeout
        urls = [
            f"http://{host}:{port}/health",
            f"http://{host}:{port}/v1/models",
        ]
        while time.time() < deadline:
            for url in urls:
                try:
                    resp = httpx.get(url, timeout=5.0)
                    if resp.status_code == 200:
                        logger.info("vllm_ready", url=url)
                        return True
                except httpx.HTTPError:
                    pass
            time.sleep(2)
        logger.warning("vllm_ready_timeout", host=host, port=port, timeout=timeout)
        return False

    def stop(self, force: bool = False) -> None:
        """停止 vLLM 进程。"""
        pid = self._read_pid()

        def _kill(target_pid: int) -> None:
            try:
                kill_signal = signal.SIGTERM
                if force:
                    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(target_pid, kill_signal)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.warning("vllm_stop_error", error=str(exc), pid=target_pid)

        if self._api_process and self._api_process.is_alive():
            try:
                self._api_process.terminate()
                self._api_process.join(timeout=5)
            except OSError as exc:
                logger.warning("vllm_stop_error", error=str(exc))
            self._api_process = None
        elif self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                if force:
                    time.sleep(1)
                    if self._process.poll() is None:
                        self._process.kill()
            except OSError as exc:
                logger.warning("vllm_stop_error", error=str(exc))
        elif pid:
            _kill(pid)

        self._remove_pid()
        if self._log_fp and not self._log_fp.closed:
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except (OSError, ValueError):
                pass
