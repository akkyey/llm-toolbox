#!/usr/bin/env python3
"""
StateForge LLM Monitor Daemon v2.0
===================================
llama-server の稼働状態とシステムリソースを統合監視するデーモン。

- journalctl ログストリームからLLM状態を追跡
- /proc, /sys からCPU・メモリ・GPU情報を定期収集
- NVIDIA GPU (nvidia-smi) 対応（未搭載時は自動スキップ）
- 日本語/英語の表示言語切り替え対応

Usage:
    python3 llama_monitor_daemon.py [--lang ja|en]
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

OUTPUT_STATUS_JSON = Path("/home/irom/.local/state/stateforge/llama_status.json")
PROXY_METRICS_JSON = Path("/home/irom/.local/state/stateforge/proxy_metrics.json")

METRICS_INTERVAL_SEC = 5  # システムメトリクス収集間隔

JST = timezone(timedelta(hours=9))

# エンジン定義（ポート → サービス名・表示名）
ENGINE_DEFS = {
    "9090": {"service": "llama-server",       "name_ja": "Local LLM (Port 9090)", "name_en": "Local LLM (Port 9090)"},
    "9091": {"service": "llama-server-coder", "name_ja": "Local LLM (Port 9091)", "name_en": "Local LLM (Port 9091)"},
}

# AMDGPU sysfs パス (動的解決へ移行)
# hwmon パス (動的解決へ移行)

# ---------------------------------------------------------------------------
# i18n（国際化）
# ---------------------------------------------------------------------------

TRANSLATIONS = {
    "ja": {
        "daemon_start":     "[+] StateForge Monitor Daemon v2.0 起動。{n}ポート並行監視を開始します...",
        "watch_start":      "[+] 監視タスク起動: Port {port} ({service})",
        "watch_reconnect":  "[!] 監視タスク再接続: Port {port} ({service}) — {sec}秒後にリトライ",
        "metrics_start":    "[+] システムメトリクス収集タスク起動（{interval}秒間隔）",
        "json_write_fail":  "[-] JSON書き出し失敗: {err}",
        "nvidia_detected":  "[+] NVIDIA GPU 検出: nvidia-smi パス = {path}",
        "nvidia_not_found": "[i] NVIDIA GPU 未検出（nvidia-smi が見つかりません）。NVIDIA監視をスキップします。",
        "pid_detected":     "[+] PID検出: Port {port} → PID {pid}",
        "pid_lost":         "[!] PID喪失: Port {port} — プロセスが見つかりません",
        "shutdown":         "\n[-] 終了要求を受け付けました。",
        "status_idle":      "待機中",
        "status_loading":   "タスク読み込み中",
        "status_prompt":    "プロンプト処理中",
        "status_generating":"回答生成中",
        "status_completed": "完了",
        "status_saving":    "キャッシュ保存中",
        "status_crashed":   "異常終了",
        "status_restarted": "再起動済み",
        "status_online":    "オンライン",
    },
    "en": {
        "daemon_start":     "[+] StateForge Monitor Daemon v2.0 started. Watching {n} ports...",
        "watch_start":      "[+] Watcher started: Port {port} ({service})",
        "watch_reconnect":  "[!] Watcher reconnecting: Port {port} ({service}) — retry in {sec}s",
        "metrics_start":    "[+] System metrics collector started ({interval}s interval)",
        "json_write_fail":  "[-] JSON write failed: {err}",
        "nvidia_detected":  "[+] NVIDIA GPU detected: nvidia-smi path = {path}",
        "nvidia_not_found": "[i] NVIDIA GPU not detected (nvidia-smi not found). Skipping NVIDIA monitoring.",
        "pid_detected":     "[+] PID detected: Port {port} → PID {pid}",
        "pid_lost":         "[!] PID lost: Port {port} — process not found",
        "shutdown":         "\n[-] Shutdown requested.",
        "status_idle":      "Idle",
        "status_loading":   "Loading Task",
        "status_prompt":    "Prompt Processing",
        "status_generating":"Generating",
        "status_completed": "Completed",
        "status_saving":    "Saving Cache",
        "status_crashed":   "Crashed",
        "status_restarted": "Restarted",
        "status_online":    "Online",
    },
}


class I18n:
    """シンプルなi18nヘルパー。キーとパラメータで翻訳文字列を返す。"""

    def __init__(self, lang: str = "ja"):
        self.lang = lang if lang in TRANSLATIONS else "ja"
        self._strings = TRANSLATIONS[self.lang]

    def t(self, key: str, **kwargs) -> str:
        template = self._strings.get(key, key)
        try:
            return template.format(**kwargs)
        except KeyError:
            return template


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------

@dataclass
class PromptMetrics:
    """プロンプト処理（プレフィル）の進捗情報"""
    progress_pct: float = 0.0
    tokens_processed: int = 0
    elapsed_seconds: float = 0.0
    tokens_per_second: float = 0.0


@dataclass
class DecodeMetrics:
    """トークン生成（デコード）の進捗情報"""
    tokens_generated: int = 0
    tokens_per_second: float = 0.0


@dataclass
class CheckpointInfo:
    """KVキャッシュのチェックポイント情報"""
    current: int = 0
    total: int = 0


@dataclass
class ProcessInfo:
    """llama-server プロセスのリソース情報"""
    pid: int = 0
    rss_mb: float = 0.0
    swap_mb: float = 0.0
    threads: int = 0
    model_name: str = ""


@dataclass
class EngineState:
    """1ポート分のLLMエンジン状態"""
    name: str = ""
    status: str = "ONLINE"
    status_label: str = ""
    reason: str = ""
    task_id: int = 0
    prompt: PromptMetrics = field(default_factory=PromptMetrics)
    decode: DecodeMetrics = field(default_factory=DecodeMetrics)
    checkpoint: CheckpointInfo = field(default_factory=CheckpointInfo)
    process: ProcessInfo = field(default_factory=ProcessInfo)
    total_tokens: int = 0
    max_context: int = 65536  # Default fallback so denominator always shows
    
    # 新規追加メトリクス
    model_load_progress: float = -1.0
    active_slots: int = 0
    context_shift: bool = False
    max_tps: float = 0.0

    def reset_for_new_task(self):
        """新しいタスクの開始時に進捗をリセット"""
        self.prompt = PromptMetrics()
        self.decode = DecodeMetrics()
        self.checkpoint = CheckpointInfo()
        self.reason = ""
        # total_tokens is kept across tasks until explicitly updated


@dataclass
class MemoryInfo:
    """システムメモリ情報"""
    total_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    swap_used_gb: float = 0.0
    swap_total_gb: float = 0.0


@dataclass
class AmdGpuInfo:
    """AMD iGPU 情報"""
    busy_percent: int = -1
    vram_used_mb: float = -1
    vram_total_mb: float = -1
    gtt_used_mb: float = -1
    gtt_total_mb: float = -1
    clock_mhz: int = -1
    temp_celsius: float = -1


@dataclass
class NvidiaGpuInfo:
    """NVIDIA GPU 情報（未搭載時は全フィールド -1）"""
    available: bool = False
    name: str = ""
    utilization_percent: int = -1
    memory_used_mb: float = -1
    memory_total_mb: float = -1
    temp_celsius: float = -1
    power_watts: float = -1
    fan_percent: int = -1


@dataclass
class SystemMetrics:
    """システム全体のメトリクス"""
    cpu_percent: float = 0.0
    cpu_temp_celsius: float = -1
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    amd_gpu: AmdGpuInfo = field(default_factory=AmdGpuInfo)
    nvidia_gpu: NvidiaGpuInfo = field(default_factory=NvidiaGpuInfo)


# ---------------------------------------------------------------------------
# ログパーサー
# ---------------------------------------------------------------------------

class LogParser:
    """llama-server の journalctl ログを解析し、EngineState を更新する。"""

    # コンパイル済み正規表現パターン
    _PATTERNS = {
        "prompt_progress": re.compile(
            r"prompt processing, n_tokens =\s*(?P<tokens>\d+),"
            r" progress = \s*(?P<prog>[\d.]+),"
            r" t = \s*(?P<time>[\d.]+) s /\s*(?P<tps>[\d.]+) tokens per second"
        ),
        "decode_progress": re.compile(
            r"n_decoded =\s*(?P<tokens>\d+),"
            r" tg =\s*(?P<tps>[\d.]+) t/s"
        ),
        "checkpoint": re.compile(
            r"created context checkpoint (?P<current>\d+) of (?P<total>\d+)"
        ),
        "task_launch": re.compile(
            r"launch_slot_.*task (?P<task_id>\d+)"
        ),
        "task_complete": re.compile(
            r"stop processing: n_tokens = (?P<n_tokens>\d+)"
        ),
        "slots_idle": re.compile(
            r"all slots are idle"
        ),
        "context_shift": re.compile(
            r"context shift|removing \d+ tokens"
        ),
        "model_load": re.compile(
            r"llama_model_load.*?:\s+(?P<progress>[\d.]+)%"
        ),
        "prompt_save": re.compile(
            r"prompt_save.*saving prompt"
        ),
        "crash": re.compile(
            r"terminate called after throwing an instance of '(?P<err>[^']+)'"
        ),
        "dev_lost": re.compile(
            r"The CS has been cancelled because the context is lost"
        ),
        "restart": re.compile(
            r"Started llama-server.*\.service"
        ),
        "cache_state": re.compile(
            r"cache state:.*limits:.*?(?P<max_ctx>\d+) tokens"
        ),
        "prompt_update": re.compile(
            r"update:\s+-\s+prompt\s+0x[0-9a-f]+:\s+(?P<tokens>\d+) tokens"
        ),
    }

    def __init__(self, i18n: I18n):
        self._i18n = i18n

    def parse(self, engine: EngineState, line: str) -> bool:
        """
        ログ行を解析して engine の状態を更新する。
        状態が変更された場合は True を返す。
        """

        # --- 復旧検知（最優先） ---
        if self._PATTERNS["restart"].search(line):
            engine.status = "RESTARTED"
            engine.status_label = self._i18n.t("status_restarted")
            engine.reason = "llama-server restarted by systemd"
            engine.reset_for_new_task()
            return True

        # --- クラッシュ検知 ---
        if m := self._PATTERNS["crash"].search(line):
            engine.status = "CRASHED"
            engine.status_label = self._i18n.t("status_crashed")
            engine.reason = f"terminate ({m.group('err')})"
            return True

        if self._PATTERNS["dev_lost"].search(line):
            engine.status = "CRASHED"
            engine.status_label = self._i18n.t("status_crashed")
            engine.reason = "DeviceLostError (Vulkan Memory/CS Cancelled)"
            return True

        # --- 状態追跡 ---
        if self._PATTERNS["context_shift"].search(line):
            engine.context_shift = True
            return True

        if m := self._PATTERNS["model_load"].search(line):
            engine.status = "LOADING"
            engine.status_label = self._i18n.t("status_loading")
            engine.model_load_progress = float(m.group("progress"))
            return True

        # --- タスク開始 ---
        if m := self._PATTERNS["task_launch"].search(line):
            engine.status = "LOADING"
            engine.status_label = self._i18n.t("status_loading")
            engine.task_id = int(m.group("task_id"))
            engine.active_slots += 1
            engine.context_shift = False # 新タスクでリセット
            engine.reset_for_new_task()
            return True

        # --- プロンプト処理（プレフィル）進捗 ---
        if m := self._PATTERNS["prompt_progress"].search(line):
            d = m.groupdict()
            engine.status = "PROMPT_PROCESSING"
            engine.status_label = self._i18n.t("status_prompt")
            engine.prompt.progress_pct = round(float(d["prog"]) * 100, 1)
            engine.prompt.tokens_processed = int(d["tokens"])
            engine.prompt.elapsed_seconds = round(float(d["time"]), 2)
            tps = round(float(d["tps"]), 2)
            engine.prompt.tokens_per_second = tps
            engine.total_tokens = engine.prompt.tokens_processed + engine.decode.tokens_generated
            # プレフィル速度も max_tps の評価対象に含める場合
            if tps > engine.max_tps:
                engine.max_tps = tps
            return True

        # --- デコード処理 ---
        if m := self._PATTERNS["decode_progress"].search(line):
            engine.status = "GENERATING"
            engine.status_label = self._i18n.t("status_generating")
            engine.decode.tokens_generated = int(m.group("tokens"))
            tps = float(m.group("tps"))
            engine.decode.tokens_per_second = tps
            if tps > engine.max_tps:
                engine.max_tps = tps
            engine.total_tokens = engine.prompt.tokens_processed + engine.decode.tokens_generated
            return True

        # --- KVキャッシュ保存 ---
        if self._PATTERNS["prompt_save"].search(line):
            engine.status = "SAVING_CACHE"
            engine.status_label = self._i18n.t("status_saving")
            return True

        # --- タスク完了 ---
        if m := self._PATTERNS["task_complete"].search(line):
            engine.status = "COMPLETED"
            engine.status_label = self._i18n.t("status_completed")
            n_tokens = int(m.group("n_tokens"))
            engine.total_tokens += n_tokens
            engine.active_slots = max(0, engine.active_slots - 1)
            return True

        # --- 全スロットアイドル ---
        if self._PATTERNS["slots_idle"].search(line):
            engine.status = "IDLE"
            engine.status_label = self._i18n.t("status_idle")
            engine.active_slots = 0
            return True

        # --- チェックポイント ---
        if m := self._PATTERNS["checkpoint"].search(line):
            d = m.groupdict()
            engine.checkpoint.current = int(d["current"])
            engine.checkpoint.total = int(d["total"])
            return True

        # --- トークン使用量 ---
        if m := self._PATTERNS["cache_state"].search(line):
            engine.max_context = int(m.group("max_ctx"))
            return True

        if m := self._PATTERNS["prompt_update"].search(line):
            engine.total_tokens = int(m.group("tokens"))
            return True

        return False


# ---------------------------------------------------------------------------
# システムメトリクス収集
# ---------------------------------------------------------------------------

class SystemMetricsCollector:
    """
    /proc と /sys を直接読み取り、システムリソース情報を収集する。
    外部コマンド依存: nvidia-smi のみ（NVIDIA GPU 搭載時）
    """

    def __init__(self, i18n: I18n, engine_pids: dict[str, int]):
        self._i18n = i18n
        self._engine_pids = engine_pids  # {"9090": PID, "9091": PID}
        self._prev_cpu_times: Optional[tuple] = None
        self._nvidia_smi_path: Optional[str] = self._detect_nvidia_smi()
        self._hwmon_cpu_temp: Optional[Path] = self._find_hwmon("k10temp", "temp1_input") or self._find_hwmon("coretemp", "temp1_input")
        self._hwmon_gpu_temp: Optional[Path] = self._find_hwmon("amdgpu", "temp1_input")
        self._amdgpu_sysfs: Optional[Path] = self._find_amdgpu_sysfs()

    def _find_hwmon(self, name: str, input_file: str) -> Optional[Path]:
        hwmon_dir = Path("/sys/class/hwmon")
        if not hwmon_dir.exists():
            return None
        try:
            for hwmon in hwmon_dir.iterdir():
                if hwmon.is_dir():
                    name_file = hwmon / "name"
                    if name_file.exists() and name_file.read_text().strip() == name:
                        target = hwmon / input_file
                        if target.exists():
                            return target
        except OSError:
            pass
        return None

    def _find_amdgpu_sysfs(self) -> Optional[Path]:
        drm_dir = Path("/sys/class/drm")
        if not drm_dir.exists():
            return None
        try:
            for card in drm_dir.glob("card*"):
                device = card / "device"
                vendor = device / "vendor"
                if vendor.exists() and vendor.read_text().strip() == "0x1002":
                    return device
        except OSError:
            pass
        return None

    # --- NVIDIA GPU 検出 ---

    def _detect_nvidia_smi(self) -> Optional[str]:
        path = shutil.which("nvidia-smi")
        if path:
            print(self._i18n.t("nvidia_detected", path=path))
        else:
            print(self._i18n.t("nvidia_not_found"))
        return path

    # --- 安全なファイル読み取りヘルパー ---

    @staticmethod
    def _read_sysfs_int(path: Path, default: int = -1) -> int:
        try:
            return int(path.read_text().strip())
        except (FileNotFoundError, ValueError, PermissionError):
            return default

    @staticmethod
    def _read_sysfs_float(path: Path, default: float = -1.0) -> float:
        try:
            return float(path.read_text().strip())
        except (FileNotFoundError, ValueError, PermissionError):
            return default

    # --- CPU 使用率（/proc/stat 差分方式） ---

    def _read_cpu_percent(self) -> float:
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            times = tuple(int(x) for x in parts[1:9])
            if self._prev_cpu_times is None:
                self._prev_cpu_times = times
                return 0.0
            deltas = tuple(a - b for a, b in zip(times, self._prev_cpu_times))
            self._prev_cpu_times = times
            total = sum(deltas)
            if total == 0:
                return 0.0
            idle = deltas[3] + deltas[4]  # idle + iowait
            return round((1 - idle / total) * 100, 1)
        except (FileNotFoundError, ValueError, IndexError):
            return 0.0

    # --- メモリ情報（/proc/meminfo） ---

    @staticmethod
    def _read_memory() -> MemoryInfo:
        info = MemoryInfo()
        try:
            data = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        data[key] = int(parts[1])  # kB

            total_kb = data.get("MemTotal", 0)
            avail_kb = data.get("MemAvailable", 0)
            swap_total_kb = data.get("SwapTotal", 0)
            swap_free_kb = data.get("SwapFree", 0)

            info.total_gb = round(total_kb / 1048576, 1)
            info.available_gb = round(avail_kb / 1048576, 1)
            info.used_gb = round((total_kb - avail_kb) / 1048576, 1)
            info.swap_total_gb = round(swap_total_kb / 1048576, 1)
            info.swap_used_gb = round((swap_total_kb - swap_free_kb) / 1048576, 1)
        except (FileNotFoundError, ValueError):
            pass
        return info

    # --- プロセス情報（/proc/<pid>/status） ---

    @staticmethod
    def _read_process_info(pid: int) -> ProcessInfo:
        pinfo = ProcessInfo(pid=pid)
        if pid <= 0:
            return pinfo
        status_path = Path(f"/proc/{pid}/status")
        try:
            text = status_path.read_text()
            for line in text.splitlines():
                if line.startswith("VmRSS:"):
                    pinfo.rss_mb = round(int(line.split()[1]) / 1024, 1)
                elif line.startswith("VmSwap:"):
                    pinfo.swap_mb = round(int(line.split()[1]) / 1024, 1)
                elif line.startswith("Threads:"):
                    pinfo.threads = int(line.split()[1])
        except (FileNotFoundError, ValueError, IndexError, PermissionError):
            pinfo.pid = 0  # プロセスが消えた

        if pinfo.pid > 0:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            try:
                cmdline = cmdline_path.read_text().split("\0")
                if "-m" in cmdline:
                    m_idx = cmdline.index("-m")
                    if m_idx + 1 < len(cmdline):
                        pinfo.model_name = os.path.basename(cmdline[m_idx + 1])
                elif "--model" in cmdline:
                    m_idx = cmdline.index("--model")
                    if m_idx + 1 < len(cmdline):
                        pinfo.model_name = os.path.basename(cmdline[m_idx + 1])
            except (FileNotFoundError, ValueError, IndexError, PermissionError):
                pass

        return pinfo

    # --- AMD iGPU（sysfs） ---

    def _read_amd_gpu(self) -> AmdGpuInfo:
        gpu = AmdGpuInfo()
        if not self._amdgpu_sysfs or not self._amdgpu_sysfs.exists():
            return gpu

        gpu.busy_percent = self._read_sysfs_int(self._amdgpu_sysfs / "gpu_busy_percent")

        vram_used = self._read_sysfs_int(self._amdgpu_sysfs / "mem_info_vram_used")
        vram_total = self._read_sysfs_int(self._amdgpu_sysfs / "mem_info_vram_total")
        if vram_used >= 0:
            gpu.vram_used_mb = round(vram_used / 1048576, 1)
        if vram_total >= 0:
            gpu.vram_total_mb = round(vram_total / 1048576, 1)

        gtt_used = self._read_sysfs_int(self._amdgpu_sysfs / "mem_info_gtt_used")
        gtt_total = self._read_sysfs_int(self._amdgpu_sysfs / "mem_info_gtt_total")
        if gtt_used >= 0:
            gpu.gtt_used_mb = round(gtt_used / 1048576, 1)
        if gtt_total >= 0:
            gpu.gtt_total_mb = round(gtt_total / 1048576, 1)

        # GPUクロック — pp_dpm_sclk の「*」付き行からパース
        try:
            text = (self._amdgpu_sysfs / "pp_dpm_sclk").read_text()
            for line in text.splitlines():
                if "*" in line:
                    m = re.search(r"(\d+)Mhz", line)
                    if m:
                        gpu.clock_mhz = int(m.group(1))
        except (FileNotFoundError, PermissionError):
            pass

        # GPU温度
        if self._hwmon_gpu_temp:
            raw = self._read_sysfs_int(self._hwmon_gpu_temp)
            if raw >= 0:
                gpu.temp_celsius = round(raw / 1000, 1)

        return gpu

    # --- NVIDIA GPU（nvidia-smi） ---

    async def _read_nvidia_gpu(self) -> NvidiaGpuInfo:
        info = NvidiaGpuInfo()
        if not self._nvidia_smi_path:
            return info

        try:
            proc = await asyncio.create_subprocess_exec(
                self._nvidia_smi_path,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,fan.speed",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            line = stdout.decode().strip()
            if not line:
                return info

            # 複数GPU対応: 先頭1台のみ（将来拡張可能）
            parts = [p.strip() for p in line.split("\n")[0].split(",")]
            if len(parts) >= 7:
                info.available = True
                info.name = parts[0]
                info.utilization_percent = int(parts[1]) if parts[1] not in ("[N/A]", "") else -1
                info.memory_used_mb = float(parts[2]) if parts[2] not in ("[N/A]", "") else -1
                info.memory_total_mb = float(parts[3]) if parts[3] not in ("[N/A]", "") else -1
                info.temp_celsius = float(parts[4]) if parts[4] not in ("[N/A]", "") else -1
                info.power_watts = float(parts[5]) if parts[5] not in ("[N/A]", "") else -1
                try:
                    info.fan_percent = int(parts[6]) if parts[6] not in ("[N/A]", "") else -1
                except ValueError:
                    info.fan_percent = -1
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            pass

        return info

    # --- CPU温度 ---

    def _read_cpu_temp(self) -> float:
        if self._hwmon_cpu_temp:
            raw = self._read_sysfs_int(self._hwmon_cpu_temp)
            if raw >= 0:
                return round(raw / 1000, 1)
        return -1

    # --- PID自動検出 ---

    async def refresh_pids(self) -> dict[str, int]:
        """各ポートの llama-server PID を pgrep で再取得"""
        new_pids = {}
        for port in self._engine_pids:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pgrep", "-f", f"llama-server.*--port {port}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                pids = stdout.decode().strip().split("\n")
                # pgrep 自身を除外して最初のPIDを取得
                pid = int(pids[0]) if pids and pids[0] else 0
                new_pids[port] = pid
            except (asyncio.TimeoutError, ValueError, IndexError, OSError):
                new_pids[port] = 0
        self._engine_pids = new_pids
        return new_pids

    # --- 統合収集 ---

    async def collect(self) -> tuple[SystemMetrics, dict[str, ProcessInfo]]:
        """全メトリクスを一括収集して返す"""
        metrics = SystemMetrics()
        metrics.cpu_percent = self._read_cpu_percent()
        metrics.cpu_temp_celsius = self._read_cpu_temp()
        metrics.memory = self._read_memory()
        metrics.amd_gpu = self._read_amd_gpu()
        metrics.nvidia_gpu = await self._read_nvidia_gpu()

        # プロセス別情報
        process_infos = {}
        for port, pid in self._engine_pids.items():
            process_infos[port] = self._read_process_info(pid)

        return metrics, process_infos


# ---------------------------------------------------------------------------
# JSON書き出し
# ---------------------------------------------------------------------------

class StatusWriter:
    """エンジン状態とシステムメトリクスを統合してJSONへアトミック書き出しする。"""

    def __init__(self, output_path: Path, i18n: I18n):
        self._output_path = output_path
        self._i18n = i18n

    def _serialize_value(self, v):
        """-1 の値を None に変換（JSONでは null、表示側では「-」にできる）"""
        if isinstance(v, (int, float)) and v == -1:
            return None
        return v

    def _clean_dict(self, d: dict) -> dict:
        """辞書内の -1 値を None に再帰的に変換"""
        cleaned = {}
        for k, v in d.items():
            if isinstance(v, dict):
                cleaned[k] = self._clean_dict(v)
            else:
                cleaned[k] = self._serialize_value(v)
        return cleaned

    def write(self, engines: dict[str, EngineState], system: Optional[SystemMetrics] = None):
        """JSON をアトミックに書き出す"""
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)

            # エンジン状態をシリアライズ
            data = {}
            for port, engine in engines.items():
                data[port] = self._clean_dict(asdict(engine))

            # システムメトリクス
            if system:
                sys_dict = self._clean_dict(asdict(system))
                if not system.nvidia_gpu.available:
                    sys_dict.pop("nvidia_gpu", None)
                data["system"] = sys_dict

            data["lang"] = self._i18n.lang

            # --- Proxy Metrics の読み取り（Graceful Degradation） ---
            # プロキシが動いていない場合やファイルが存在しない場合は
            # エラーにせず、単にスキップする（モニター単体での利用を妨げない）
            try:
                if PROXY_METRICS_JSON.exists():
                    with open(PROXY_METRICS_JSON, "r", encoding="utf-8") as pf:
                        proxy_data = json.load(pf)
                    data["proxy"] = proxy_data.get("proxies", {})
            except (json.JSONDecodeError, IOError, OSError):
                pass  # Graceful Degradation: プロキシ情報は取得できなくても問題なし

            data["updated_at"] = datetime.now(JST).isoformat(timespec="seconds")

            tmp_path = self._output_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(self._output_path))

        except Exception as e:
            print(self._i18n.t("json_write_fail", err=str(e)), file=sys.stderr)


# ---------------------------------------------------------------------------
# Journalctl ウォッチャー
# ---------------------------------------------------------------------------

class JournalWatcher:
    """systemd journalctl のログストリームを非同期で監視する。"""

    def __init__(
        self,
        port: str,
        service_name: str,
        engine: EngineState,
        parser: LogParser,
        write_callback,
        i18n: I18n,
    ):
        self._port = port
        self._service = service_name
        self._engine = engine
        self._parser = parser
        self._write_callback = write_callback
        self._i18n = i18n

    async def run(self):
        """ログストリームを永続的に監視する（切断時は自動再接続）"""
        print(self._i18n.t("watch_start", port=self._port, service=self._service))

        while True:
            try:
                await self._watch_stream()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            # 再接続待機
            retry_sec = 5
            print(self._i18n.t("watch_reconnect", port=self._port, service=self._service, sec=retry_sec))
            await asyncio.sleep(retry_sec)

    async def _watch_stream(self):
        cmd = ["journalctl", "-u", self._service, "-f", "-n", "0"]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if self._parser.parse(self._engine, line):
                    self._write_callback()
        finally:
            try:
                process.terminate()
                await process.wait()
            except ProcessLookupError:
                pass


# ---------------------------------------------------------------------------
# メインオーケストレーター
# ---------------------------------------------------------------------------

class MonitorDaemon:
    """
    全コンポーネントを統合して管理するメインクラス。
    - JournalWatcher × N ポート（ログ監視）
    - SystemMetricsCollector（定期リソース収集）
    - StatusWriter（JSON統合書き出し）
    """

    def __init__(self, lang: str = "ja", ports: list[str] = None):
        self._i18n = I18n(lang)
        self._engines: dict[str, EngineState] = {}
        self._system_metrics = SystemMetrics()
        
        self.ports = ports or ["9090", "9091"]
        self._engine_defs = {}
        for port in self.ports:
            if port == "9090":
                self._engine_defs[port] = {"service": "llama-server", "name_ja": "Local LLM (Port 9090)", "name_en": "Local LLM (Port 9090)"}
            elif port == "9091":
                self._engine_defs[port] = {"service": "llama-server-coder", "name_ja": "Local LLM (Port 9091)", "name_en": "Local LLM (Port 9091)"}
            else:
                self._engine_defs[port] = {"service": f"llama-server@{port}", "name_ja": f"Local LLM (Port {port})", "name_en": f"Local LLM (Port {port})"}

        # エンジン初期化
        name_key = "name_ja" if lang == "ja" else "name_en"
        for port, edef in self._engine_defs.items():
            engine = EngineState(
                name=edef[name_key],
                status="ONLINE",
                status_label=self._i18n.t("status_online"),
            )
            self._engines[port] = engine

        self._parser = LogParser(self._i18n)
        self._writer = StatusWriter(OUTPUT_STATUS_JSON, self._i18n)

    def write_status(self):
        """全体のステータスを JSON に書き出す"""
        self._writer.write(self._engines, self._system_metrics)

    async def run(self):
        print(self._i18n.t("daemon_start", n=len(self.ports)))

        self._collector = SystemMetricsCollector(self._i18n, {port: 0 for port in self.ports})

        # PID 初期検出
        initial_pids = await self._collector.refresh_pids()
        for port, pid in initial_pids.items():
            self._engines[port].process.pid = pid
            if pid:
                print(self._i18n.t("pid_detected", port=port, pid=pid))
            else:
                print(self._i18n.t("pid_lost", port=port))

        # 初期状態の書き出し
        self.write_status()

        # タスク群を生成
        tasks = []

        # ログウォッチャー
        for port, edef in self._engine_defs.items():
            watcher = JournalWatcher(
                port=port,
                service_name=edef["service"],
                engine=self._engines[port],
                parser=self._parser,
                write_callback=self.write_status,
                i18n=self._i18n,
            )
            tasks.append(asyncio.create_task(watcher.run()))

        # システムメトリクス定期収集
        tasks.append(asyncio.create_task(self._metrics_loop()))

        await asyncio.gather(*tasks)

    async def _metrics_loop(self):
        """システムメトリクスを定期的に収集してJSONに書き出す"""
        print(self._i18n.t("metrics_start", interval=METRICS_INTERVAL_SEC))

        # PIDリフレッシュカウンター（60秒ごとに再検出）
        refresh_counter = 0
        refresh_interval = 60 // METRICS_INTERVAL_SEC

        while True:
            try:
                # 定期的にPIDをリフレッシュ
                refresh_counter += 1
                if refresh_counter >= refresh_interval:
                    new_pids = await self._collector.refresh_pids()
                    for port, pid in new_pids.items():
                        self._engines[port].process.pid = pid
                    refresh_counter = 0

                # メトリクス収集
                self._system_metrics, process_infos = await self._collector.collect()

                # プロセス情報をエンジンに反映
                for port, pinfo in process_infos.items():
                    self._engines[port].process = pinfo
                    if pinfo.model_name:
                        self._engines[port].name = pinfo.model_name
                    else:
                        name_key = "name_ja" if self._i18n.lang == "ja" else "name_en"
                        self._engines[port].name = self._engine_defs[port][name_key]

                # 統合書き出し
                self.write_status()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[!] metrics error: {e}", file=sys.stderr)

            await asyncio.sleep(METRICS_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="StateForge LLM Monitor Daemon v2.0"
    )
    parser.add_argument(
        "--lang", choices=["ja", "en"], default="ja",
        help="表示言語 / Display language (default: ja)"
    )
    parser.add_argument(
        "--ports", type=str, default="9090,9091",
        help="監視対象ポート (カンマ区切り) / Ports to monitor (comma separated) (default: 9090,9091)"
    )
    args = parser.parse_args()

    ports = [p.strip() for p in args.ports.split(",") if p.strip()]

    daemon = MonitorDaemon(lang=args.lang, ports=ports)

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        i18n = I18n(args.lang)
        print(i18n.t("shutdown"))


if __name__ == "__main__":
    main()
