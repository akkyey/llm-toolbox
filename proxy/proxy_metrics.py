#!/usr/bin/env python3
"""
Proxy Metrics Collector
========================
プロキシの「有効性」を計測し、JSONファイルに書き出すモジュール。
Monitor（Daemon/TUI）がこのJSONを読み取ってダッシュボードに表示する。

出力先: ~/.local/state/stateforge/proxy_metrics.json (PROXY_METRICS_JSON で上書き可能)
"""

import json
import os
import time
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

# Monitor Daemon と同じ状態ディレクトリに出力（読み取り側の実装を簡素化）
_DEFAULT_METRICS_PATH = str(Path.home() / ".local" / "state" / "stateforge" / "proxy_metrics.json")
OUTPUT_METRICS_JSON = Path(
    os.getenv("PROXY_METRICS_JSON", _DEFAULT_METRICS_PATH)
)


@dataclass
class ProxyInstanceMetrics:
    """1つのプロキシインスタンスのメトリクス"""
    name: str = ""
    port: int = 0
    pid: int = 0
    uptime_seconds: float = 0.0
    active_requests: int = 0
    total_requests: int = 0
    total_errors: int = 0

    # Kilo Proxy 固有: プロンプト圧縮量
    tokens_saved_by_compression: int = 0
    compression_invocations: int = 0

    # Aider Proxy 固有: 不当文字列の補正回数
    payload_corrections: int = 0
    fence_standardizations: int = 0
    hallucination_fixes: int = 0

    # 共通: ツールコール修復回数（kilo_proxy の fix_tool_calls）
    tool_call_fixes: int = 0

    # 共通: Heartbeat 送信回数
    heartbeats_sent: int = 0

    # 共通: キャッシュ崩壊の安全装置（Safeguard）発動回数
    safeguard_activations: int = 0

    # 最後のリクエスト情報
    last_request_at: str = ""
    last_error: str = ""


class ProxyMetricsCollector:
    """
    スレッドセーフなメトリクス収集器。
    各プロキシ（aider_proxy, kilo_proxy）から呼び出され、
    定期的にJSONファイルへフラッシュする。
    """

    def __init__(self, proxy_name: str, port: int):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._metrics = ProxyInstanceMetrics(
            name=proxy_name,
            port=port,
            pid=os.getpid(),
        )
        # バックグラウンドでの定期フラッシュを開始（5秒間隔）
        self._flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self._flush_thread.start()

    def record_request(self):
        """リクエスト受信時に呼ぶ"""
        with self._lock:
            self._metrics.total_requests += 1
            self._metrics.active_requests += 1
            self._metrics.last_request_at = datetime.now(JST).isoformat(timespec="seconds")

    def record_request_complete(self):
        """リクエスト完了時に呼ぶ"""
        with self._lock:
            self._metrics.active_requests = max(0, self._metrics.active_requests - 1)

    def record_error(self, error_msg: str = ""):
        """エラー発生時に呼ぶ"""
        with self._lock:
            self._metrics.total_errors += 1
            self._metrics.last_error = error_msg[:200]  # 長すぎるエラーは切り詰め

    def record_compression(self, original_chars: int, compressed_chars: int):
        """Kilo Proxy: Headroom圧縮の実行結果を記録"""
        with self._lock:
            self._metrics.compression_invocations += 1
            # おおよそ 1トークン ≈ 3文字 として概算
            saved_tokens = max(0, (original_chars - compressed_chars)) // 3
            self._metrics.tokens_saved_by_compression += saved_tokens

    def record_payload_correction(self):
        """Aider Proxy: ペイロード補正を記録"""
        with self._lock:
            self._metrics.payload_corrections += 1

    def record_fence_standardization(self):
        """Aider Proxy: フェンス（バッククォート）の統一を記録"""
        with self._lock:
            self._metrics.fence_standardizations += 1

    def record_hallucination_fix(self):
        """Aider Proxy: ハルシネーション（改行漏れ）修復を記録"""
        with self._lock:
            self._metrics.hallucination_fixes += 1

    def record_tool_call_fix(self):
        """Kilo Proxy: ツールコール形式の修復を記録"""
        with self._lock:
            self._metrics.tool_call_fixes += 1

    def record_heartbeat(self):
        """Heartbeat送信を記録"""
        with self._lock:
            self._metrics.heartbeats_sent += 1

    def record_safeguard(self):
        """安全装置の発動を記録"""
        with self._lock:
            self._metrics.safeguard_activations += 1

    def flush(self):
        """現在のメトリクスをJSONファイルに書き出す"""
        with self._lock:
            self._metrics.uptime_seconds = time.time() - self._start_time
            snapshot = asdict(self._metrics)

        try:
            OUTPUT_METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
            
            import fcntl
            lock_path = OUTPUT_METRICS_JSON.with_suffix(".lock")
            with open(lock_path, "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)
                try:
                    # 既存のJSONを読み込み、自分のプロキシのエントリだけを更新する
                    existing = {}
                    if OUTPUT_METRICS_JSON.exists():
                        try:
                            with open(OUTPUT_METRICS_JSON, "r", encoding="utf-8") as f:
                                existing = json.load(f)
                        except (json.JSONDecodeError, IOError):
                            existing = {}

                    # プロキシ名をキーとして自分のメトリクスを書き込む
                    if "proxies" not in existing:
                        existing["proxies"] = {}
                    existing["proxies"][self._metrics.name] = snapshot
                    existing["updated_at"] = datetime.now(JST).isoformat(timespec="seconds")

                    # アトミック書き込み（tmpファイル名は競合回避のため一意にする）
                    tmp_path = OUTPUT_METRICS_JSON.with_suffix(f".{self._metrics.name}.json.tmp")
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(existing, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(str(tmp_path), str(OUTPUT_METRICS_JSON))
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[METRICS FLUSH ERROR] {e}")

    def _periodic_flush(self):
        """5秒ごとにJSONへフラッシュするバックグラウンドループ"""
        while True:
            time.sleep(5)
            self.flush()
