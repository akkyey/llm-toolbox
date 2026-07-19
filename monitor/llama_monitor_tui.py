#!/usr/bin/env python3
"""
StateForge TUI Monitor v1.0
=============================
llama_status.json を定期読み取りしてコンパクトなダッシュボードを表示する。

依存: Python 3.8+ 標準ライブラリのみ（curses）
Usage:
    python3 llama_monitor_tui.py [--interval 2] [--json /path/to/llama_status.json]
    
    q / Ctrl+C : 終了
    l          : 言語切替 (ja ↔ en)
"""

import argparse
import curses
import json
import os
import sys
import unicodedata
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DEFAULT_JSON_PATH = Path(
    os.environ.get("STATEFORGE_STATUS_JSON",
                   str(Path.home() / ".local" / "state" / "stateforge" / "llama_status.json"))
)
DEFAULT_INTERVAL = 2  # 秒

# ステータス → 表示用記号・色ID マッピング
STATUS_STYLE = {
    "IDLE":              {"symbol": "●", "ascii": "*", "color": 1},   # green
    "ONLINE":            {"symbol": "●", "ascii": "*", "color": 1},   # green
    "LOADING":           {"symbol": "◐", "ascii": "-", "color": 3},   # yellow
    "PROMPT_PROCESSING": {"symbol": "◑", "ascii": "~", "color": 3},   # yellow
    "GENERATING":        {"symbol": "▶", "ascii": ">", "color": 6},   # cyan
    "COMPLETED":         {"symbol": "✔", "ascii": "V", "color": 1},   # green
    "SAVING_CACHE":      {"symbol": "◎", "ascii": "@", "color": 5},   # magenta
    "CRASHED":           {"symbol": "✘", "ascii": "X", "color": 2},   # red
    "RESTARTED":         {"symbol": "↻", "ascii": "R", "color": 3},   # yellow
    "OFFLINE":           {"symbol": "○", "ascii": "O", "color": 8},   # dim
}

# i18n ラベル
LABELS = {
    "ja": {
        "title":       "StateForge LLM Monitor",
        "system":      "システム",
        "cpu":         "CPU",
        "mem":         "メモリ",
        "swap":        "Swap",
        "igpu":        "iGPU",
        "nvidia":      "NVIDIA GPU",
        "vram":        "VRAM",
        "gtt":         "GTT",
        "clock":       "クロック",
        "temp":        "温度",
        "power":       "電力",
        "fan":         "ファン",
        "status":      "状態",
        "prompt":      "プロンプト",
        "gen":         "生成",
        "tokens":      "トークン",
        "speed":       "速度",
        "elapsed":     "経過",
        "pid":         "PID",
        "rss":         "RSS",
        "threads":     "スレッド",
        "task":        "タスク",
        "checkpoint":  "チェックポイント",
        "updated":     "最終更新",
        "no_data":     "データなし — JSON読み込み待機中...",
        "read_err":    "読み込みエラー",
        "key_hint":    "q:終了  l:言語切替  a:ASCII",
        "busy":        "負荷",
        "usage":       "使用",
        "errors":      "エラー",
        "not_avail":   "—",
        "total_ctx":   "総記憶",
        "status_idle":      "待機中",
        "status_loading":   "タスク読み込み中",
        "status_prompt":    "プロンプト処理中",
        "status_generating":"回答生成中",
        "status_completed": "完了",
        "status_saving":    "キャッシュ保存中",
        "status_crashed":   "異常終了",
        "status_restarted": "再起動済み",
        "status_online":    "オンライン",
        "status_offline":   "オフライン",
        "proxy":       "プロキシ",
        "proxy_up":    "稼働中",
        "proxy_down":  "停止中",
        "compression": "圧縮量",
        "corrections": "補正",
        "saved":       "節約",
        "requests":    "リクエスト",
        "uptime":      "稼働",
        "interventions":"介入",
        "safeguard":   "安全装置",
        "tool_fixes":  "Tool修復",
        "fences":      "Fence修正",
        "halluc":      "改行修復",
    },
    "en": {
        "title":       "StateForge LLM Monitor",
        "system":      "System",
        "cpu":         "CPU",
        "mem":         "Memory",
        "swap":        "Swap",
        "igpu":        "iGPU",
        "nvidia":      "NVIDIA GPU",
        "vram":        "VRAM",
        "gtt":         "GTT",
        "clock":       "Clock",
        "temp":        "Temp",
        "power":       "Power",
        "fan":         "Fan",
        "status":      "Status",
        "prompt":      "Prompt",
        "gen":         "Gen",
        "tokens":      "tokens",
        "speed":       "Speed",
        "elapsed":     "Elapsed",
        "pid":         "PID",
        "rss":         "RSS",
        "threads":     "Threads",
        "task":        "Task",
        "checkpoint":  "Checkpoint",
        "updated":     "Updated",
        "no_data":     "No data — waiting for JSON...",
        "read_err":    "Read error",
        "key_hint":    "q:Quit  l:Toggle lang  a:ASCII",
        "busy":        "Busy",
        "usage":       "Used",
        "errors":      "Errors",
        "not_avail":   "—",
        "total_ctx":   "Total Ctx",
        "status_idle":      "Idle",
        "status_loading":   "Loading Task",
        "status_prompt":    "Prompt Processing",
        "status_generating":"Generating",
        "status_completed": "Completed",
        "status_saving":    "Saving Cache",
        "status_crashed":   "Crashed",
        "status_restarted": "Restarted",
        "status_online":    "Online",
        "status_offline":   "Offline",
        "proxy":       "Proxy",
        "proxy_up":    "Active",
        "proxy_down":  "Inactive",
        "compression": "Compression",
        "corrections": "Corrections",
        "saved":       "Saved",
        "requests":    "Requests",
        "uptime":      "Uptime",
        "interventions":"Interventions",
        "safeguard":   "Safeguard",
        "tool_fixes":  "Tool Fixes",
        "fences":      "Fence Fixes",
        "halluc":      "Newline Fixes",
    },
}


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

def fmt_val(v, unit="", decimals=1):
    """値をフォーマット。None や -1 は '—' を返す。"""
    if v is None or v == -1:
        return "—"
    if isinstance(v, float):
        return f"{v:.{decimals}f}{unit}"
    return f"{v}{unit}"


def fmt_mem(used, total, unit="GB"):
    """メモリ使用量を 'used/total GB' 形式にフォーマット"""
    u = fmt_val(used, "", 1)
    t = fmt_val(total, "", 1)
    return f"{u}/{t}{unit}"


def make_bar(pct, width=20, ascii_mode=False):
    """パーセンテージからプログレスバー文字列を生成"""
    if pct is None or pct < 0:
        return "—"
    filled = int(pct / 100 * width)
    if ascii_mode:
        return "[" + "=" * filled + " " * (width - filled) + f"] {pct:.0f}%"
    return "▓" * filled + "░" * (width - filled) + f" {pct:.0f}%"


def color_for_pct(pct):
    """パーセンテージに応じた色IDを返す"""
    if pct is None or pct < 0:
        return 0
    if pct < 60:
        return 1  # green
    if pct < 85:
        return 3  # yellow
    return 2  # red


def color_for_usage(used, total):
    """使用量と最大値から色IDを返す"""
    if used is None or total is None or total <= 0:
        return 0
    pct = (used / total) * 100
    if pct < 80:
        return 1
    if pct < 95:
        return 3
    return 2


def display_width(s):
    """文字列のターミナル上の表示幅を計算する（全角文字を考慮）"""
    width = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('W', 'F'):
            width += 2
        else:
            width += 1
    return width


# ---------------------------------------------------------------------------
# TUI描画クラス
# ---------------------------------------------------------------------------

class MonitorTUI:
    def __init__(self, json_path: Path, interval: float, initial_lang: str = "ja", ascii_mode: bool = False):
        self.json_path = json_path
        self.interval = interval
        self.lang = initial_lang
        self.ascii_mode = ascii_mode
        self.data = None
        self.last_error = ""

    def run(self, stdscr):
        """curses メインループ"""
        self._setup_colors()
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(int(self.interval * 1000))

        while True:
            # キー入力
            try:
                key = stdscr.getch()
                if key in (ord("q"), ord("Q")):
                    break
                elif key in (ord("l"), ord("L")):
                    self.lang = "en" if self.lang == "ja" else "ja"
                elif key in (ord("a"), ord("A")):
                    self.ascii_mode = not self.ascii_mode
            except curses.error:
                pass

            # データ読み込み
            self._load_json()

            # 描画
            stdscr.erase()
            try:
                self._draw(stdscr)
            except curses.error:
                pass  # ターミナルが小さすぎる場合は無視
            stdscr.refresh()

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        # 1=green, 2=red, 3=yellow, 4=blue, 5=magenta, 6=cyan, 7=white-on-blue
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_BLUE, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_CYAN, -1)
        curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)
        # 8=dim (dark gray)
        try:
            curses.init_pair(8, 8, -1)  # COLOR_DARKGRAY if supported
        except curses.error:
            curses.init_pair(8, curses.COLOR_WHITE, -1)

    def _load_json(self):
        try:
            text = self.json_path.read_text(encoding="utf-8")
            self.data = json.loads(text)
            self.last_error = ""
        except FileNotFoundError:
            self.data = None
            self.last_error = "JSON file not found"
        except json.JSONDecodeError:
            self.last_error = "JSON parse error"
        except Exception as e:
            self.last_error = str(e)

    def _l(self, key):
        """現在の言語でラベルを取得"""
        return LABELS.get(self.lang, LABELS["ja"]).get(key, key)

    def _draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        
        if h < 6 or w < 40:
            try:
                stdscr.addstr(0, 0, "Please enlarge terminal (Min 40x6)"[:w-1])
            except curses.error:
                pass
            return
            
        L = self._l
        row = 0

        # --- タイトルバー ---
        title = f" {L('title')} "
        titlebar = title.center(w)
        stdscr.addnstr(row, 0, titlebar, w, curses.color_pair(7) | curses.A_BOLD)
        row += 1

        if self.data is None:
            msg = L("no_data")
            if self.last_error:
                msg += f" ({self.last_error})"
            stdscr.addnstr(row + 1, 2, msg, w - 4, curses.color_pair(3))
            return

        # --- システムメトリクス ---
        sys_data = self.data.get("system", {})
        if sys_data:
            row = self._draw_system(stdscr, row, w, sys_data)

        # --- エンジン ---
        # JSONデータからポートキーを動的に取得（非ポートキーを除外）
        non_port_keys = {"system", "lang", "updated_at", "proxy"}
        ports = sorted(k for k in self.data if k not in non_port_keys)
        for port in ports:
            engine = self.data.get(port)
            if not engine:
                continue
            # OFFLINEまたはPID未検出のエンジンはスキップ
            status = engine.get("status", "ONLINE")
            pid = engine.get("process", {}).get("pid", 0)
            if status == "OFFLINE" or (status == "ONLINE" and (not pid or pid <= 0)):
                continue
            row = self._draw_engine(stdscr, row, w, port, engine)

        # --- プロキシメトリクス ---
        proxy_data = self.data.get("proxy", {})
        if proxy_data:
            row = self._draw_proxy(stdscr, row, w, proxy_data)

        # --- フッター ---
        row += 1
        if row < h:
            updated = self.data.get("updated_at", "—")
            footer_left = f" {L('key_hint')}"
            footer_right = f"{L('updated')}: {updated} "
            # エラー表示
            if self.last_error:
                footer_left += f"  [{L('read_err')}: {self.last_error}]"

            stdscr.addnstr(row, 0, " " * w, w, curses.color_pair(7))
            stdscr.addnstr(row, 0, footer_left, w, curses.color_pair(7))
            if display_width(footer_right) < w:
                stdscr.addnstr(row, w - display_width(footer_right), footer_right, len(footer_right), curses.color_pair(7))

    def _draw_segments(self, stdscr, row, w, segments):
        """セグメント（テキスト、色）のリストを折り返しながら描画する"""
        x = 0
        h = stdscr.getmaxyx()[0]
        for text, color in segments:
            if not text:
                continue
            text = text + " "
            dw = display_width(text)
            if x > 0 and x + dw > w:
                row += 1
                x = 2
            if row < h:
                try:
                    stdscr.addstr(row, x, text, curses.color_pair(color))
                except curses.error:
                    pass
            x += dw
        return row + 1

    def _draw_system(self, stdscr, row, w, sys_data):
        L = self._l
        NA = "—"
        row += 1

        # ─── システム ───
        line_char = "-" if self.ascii_mode else "─"
        header = f"{line_char * 2} {L('system')} "
        header += line_char * max(0, w - display_width(header) - 1)
        if row < stdscr.getmaxyx()[0]:
            stdscr.addnstr(row, 0, header, w - 1, curses.color_pair(4) | curses.A_BOLD)
        row += 1

        cpu_pct = sys_data.get("cpu_percent", 0)
        cpu_temp = sys_data.get("cpu_temp_celsius")
        mem = sys_data.get("memory", {})
        swap_used = mem.get("swap_used_gb")
        amd = sys_data.get("amd_gpu", {})
        nvidia = sys_data.get("nvidia_gpu")

        temp_unit = "C" if self.ascii_mode else "°C"
        cpu_str = f"[{L('cpu')}:{fmt_val(cpu_pct, '%', 0)} {fmt_val(cpu_temp, temp_unit, 0) if cpu_temp and cpu_temp >= 0 else ''}]"
        mem_str = f"[{L('mem')}:{fmt_mem(mem.get('used_gb'), mem.get('total_gb'))}]"
        swap_str = f"[{L('swap')}:{fmt_val(swap_used, 'GB', 1)}]"
        
        segments = [
            (cpu_str, color_for_pct(cpu_pct)),
            (mem_str, color_for_usage(mem.get('used_gb'), mem.get('total_gb'))),
            (swap_str, color_for_usage(swap_used, mem.get('swap_total_gb'))),
        ]

        if amd and amd.get("busy_percent", -1) >= 0:
            vram_used = amd.get('vram_used_mb')
            vram_total = amd.get('vram_total_mb')
            igpu_str = f"[{L('igpu')}:{fmt_val(amd.get('busy_percent'), '%')} {fmt_val(amd.get('temp_celsius'), temp_unit, 0) if amd.get('temp_celsius', -1) >= 0 else ''} VRAM:{fmt_mem(vram_used, vram_total, 'MB')}]"
            segments.append((igpu_str, color_for_usage(vram_used, vram_total)))

        if nvidia and nvidia.get("available"):
            nv_used = nvidia.get('memory_used_mb')
            nv_total = nvidia.get('memory_total_mb')
            nv_str = f"[{nvidia.get('name', 'NVIDIA')}:{fmt_val(nvidia.get('utilization_percent'), '%')} {fmt_val(nvidia.get('temp_celsius'), temp_unit, 0) if nvidia.get('temp_celsius', -1) >= 0 else ''} VRAM:{fmt_mem(nv_used, nv_total, 'MB')}]"
            segments.append((nv_str, color_for_usage(nv_used, nv_total)))

        return self._draw_segments(stdscr, row, w, segments)

    def _draw_engine(self, stdscr, row, w, port, engine):
        L = self._l
        h = stdscr.getmaxyx()[0]
        NA = "—"
        row += 1

        status = engine.get("status", "ONLINE")
        style = STATUS_STYLE.get(status, {"symbol": "?", "ascii": "?", "color": 0})
        sym = style["ascii"] if self.ascii_mode else style["symbol"]
        
        localized_key = f"status_{status.lower()}"
        status_label = L(localized_key)
        if status_label == localized_key:
            status_label = engine.get("status_label", status)
        
        name = engine.get("name", f"Port {port}")

        # --- エンジンヘッダー ---
        line_char = "-" if self.ascii_mode else "─"
        header = f"{line_char * 2} {sym} {name} "
        header += f":{port} "
        header += line_char * max(0, w - display_width(header) - 1)
        if row < h:
            stdscr.addnstr(row, 0, header, w - 1, curses.color_pair(style["color"]) | curses.A_BOLD)
        row += 1

        pid = engine.get("process", {}).get("pid", 0)
        rss = engine.get("process", {}).get("rss_mb", 0)
        total_tok = engine.get("total_tokens", 0)
        max_ctx = engine.get("max_context", 0)
        active_slots = engine.get("active_slots", 0)
        max_tps = engine.get("max_tps", 0.0)
        ctx_shift = engine.get("context_shift", False)
        load_prog = engine.get("model_load_progress", -1.0)
        
        if max_ctx > 0:
            total_str = f"{total_tok}/{max_ctx}"
            ctx_color = color_for_usage(total_tok, max_ctx)
        else:
            total_str = f"{total_tok}"
            ctx_color = 0

        segments = [
            (f"Status:{status_label}", style["color"] | curses.A_BOLD),
            (f"PID:{pid if pid else NA}", 0),
            (f"RAM:{fmt_val(rss/1024 if rss else 0, 'GB', 1)}", 0),
            (f"{L('total_ctx')}:{total_str}", ctx_color),
        ]

        if active_slots > 0:
            segments.append((f"Slots:{active_slots}", 6))
        if max_tps > 0:
            segments.append((f"Max:{fmt_val(max_tps, 't/s', 1)}", 0))
        if ctx_shift:
            segments.append(("[Context Shift!]", 3 | curses.A_REVERSE)) # Warning!
        if status == "LOADING" and load_prog is not None and load_prog >= 0:
            segments.append((f"[Load:{load_prog}%]", 3))

        row = self._draw_segments(stdscr, row, w, segments)

        # --- プロンプト・生成進捗（アクティブな場合のみコンパクトに） ---
        prompt = engine.get("prompt", {})
        decode = engine.get("decode", {})
        
        pp_tok = prompt.get("tokens_processed", 0)
        gen_tok = decode.get("tokens_generated", 0)
        
        prog_segments = []
        if status in ("PROMPT_PROCESSING", "LOADING") and pp_tok > 0:
            bar = make_bar(prompt.get("progress_pct", 0), 10, self.ascii_mode)
            prog_segments.append((f"{L('prompt')}:{bar} {fmt_val(prompt.get('tokens_per_second', 0), 't/s', 1)}", 3))
        elif pp_tok > 0:
            prog_segments.append((f"{L('prompt')}:{pp_tok}t ({fmt_val(prompt.get('tokens_per_second', 0), 't/s', 1)})", 8))

        if status == "GENERATING" and gen_tok > 0:
            prog_segments.append((f"{L('gen')}:{gen_tok}t {fmt_val(decode.get('tokens_per_second', 0), 't/s', 1)}", 6 | curses.A_BOLD))
        elif gen_tok > 0:
            prog_segments.append((f"{L('gen')}:{gen_tok}t ({fmt_val(decode.get('tokens_per_second', 0), 't/s', 1)})", 8))
            
        if prog_segments:
            row = self._draw_segments(stdscr, row, w, prog_segments)

        errors = engine.get("recent_errors", [])
        if errors:
            warn_sym = "!" if self.ascii_mode else "⚠"
            if row < h:
                stdscr.addnstr(row, 0, f"  {warn_sym} {errors[0]}", w - 1, curses.color_pair(2))
                row += 1

        return row

    def _draw_proxy(self, stdscr, row, w, proxy_data):
        """プロキシのメトリクスパネルを描画する"""
        L = self._l
        h = stdscr.getmaxyx()[0]
        row += 1

        line_char = "-" if self.ascii_mode else "─"
        header = f"{line_char * 2} {L('proxy')} "
        header += line_char * max(0, w - display_width(header) - 1)
        if row < h:
            stdscr.addnstr(row, 0, header, w - 1, curses.color_pair(5) | curses.A_BOLD)
        row += 1

        for name, metrics in proxy_data.items():
            if not isinstance(metrics, dict):
                continue
            if row >= h - 2:
                break

            port = metrics.get("port", "?")
            total_req = metrics.get("total_requests", 0)
            total_err = metrics.get("total_errors", 0)
            active = metrics.get("active_requests", 0)
            uptime_sec = metrics.get("uptime_seconds", 0)

            if uptime_sec > 3600:
                uptime_str = f"{uptime_sec / 3600:.1f}h"
            elif uptime_sec > 60:
                uptime_str = f"{uptime_sec / 60:.0f}m"
            else:
                uptime_str = f"{uptime_sec:.0f}s"

            status_color = 1 if total_err == 0 else 3

            sym = "*" if self.ascii_mode else "●"
            segments = [
                (f"{sym} {name} :{port}", 5),
                (f"{L('proxy_up')}", status_color),
                (f"{L('uptime')}:{uptime_str}", 0),
                (f"{L('requests')}:{total_req}", 0),
            ]
            if total_err > 0:
                segments.append((f"{L('errors')}:{total_err}", 2))
            if active > 0:
                segments.append((f"Active:{active}", 6))

            row = self._draw_segments(stdscr, row, w, segments)

            intervention_segments = []

            tokens_saved = metrics.get("tokens_saved_by_compression", 0)
            if tokens_saved > 0:
                if tokens_saved >= 1000:
                    saved_str = f"{tokens_saved / 1000:.1f}k"
                else:
                    saved_str = str(tokens_saved)
                intervention_segments.append((f"  {L('compression')}:{saved_str} tokens {L('saved')}", 5))

            comp_count = metrics.get("compression_invocations", 0)
            if comp_count > 0:
                intervention_segments.append((f"({comp_count}x)", 8))

            tool_fixes = metrics.get("tool_call_fixes", 0)
            if tool_fixes > 0:
                intervention_segments.append((f"{L('tool_fixes')}:{tool_fixes}", 5))

            safeguards = metrics.get("safeguard_activations", 0)
            if safeguards > 0:
                intervention_segments.append((f"{L('safeguard')}:{safeguards}", 2))

            corrections = metrics.get("payload_corrections", 0)
            if corrections > 0:
                intervention_segments.append((f"  {L('corrections')}:{corrections}", 5))

            fences = metrics.get("fence_standardizations", 0)
            if fences > 0:
                intervention_segments.append((f"{L('fences')}:{fences}", 5))

            halluc = metrics.get("hallucination_fixes", 0)
            if halluc > 0:
                intervention_segments.append((f"{L('halluc')}:{halluc}", 5))

            if intervention_segments:
                row = self._draw_segments(stdscr, row, w, intervention_segments)

        return row


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="StateForge TUI Monitor")
    parser.add_argument(
        "--json", type=str,
        default=str(DEFAULT_JSON_PATH),
        help=f"JSON file path (default: {DEFAULT_JSON_PATH})"
    )
    parser.add_argument(
        "--interval", type=float,
        default=DEFAULT_INTERVAL,
        help=f"Refresh interval in seconds (default: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--lang", choices=["ja", "en"],
        default="ja",
        help="Display language (default: ja)"
    )
    parser.add_argument(
        "--ascii", action="store_true",
        help="Use ASCII mode for symbols to prevent terminal width issues"
    )
    args = parser.parse_args()

    tui = MonitorTUI(
        json_path=Path(args.json),
        interval=args.interval,
        initial_lang=args.lang,
        ascii_mode=args.ascii
    )

    try:
        curses.wrapper(tui.run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
