# Local LLM Toolbox (StateForge)

A unified toolkit for running, routing, and monitoring local LLMs (like llama.cpp) seamlessly with AI coding agents like Aider and Cline.

[English](#english) | [日本語](#日本語)

---

## English

This repository contains a unified toolkit designed to stabilize, optimize, and monitor autonomous AI coding agents when running against local LLMs (e.g., llama.cpp).

### Included Components & Their Differences
Because each agent client interacts with the LLM differently, this repository includes specialized components tailored to their specific quirks:

1. **Proxy (`proxy/`)**: Intelligent payload routers tailored to specific clients.
   - **`kilo_proxy.py` (For Cline / Kilo Code)**:
     - **Dynamic Prefix Masking**: Cline sends a massive workspace file tree and timestamps in every system prompt. This proxy forcefully masks these dynamic strings to protect the KV Cache, reducing subsequent prompt processing times to near zero.
     - **Thought Pruning**: Automatically detects and strips out verbose "reasoning/apology loops" from smaller models to save context length and prevent AI degradation.
     - **Tool Call Normalization**: Aggressively formats messy outputs (e.g., XML tags or Python-style syntax) into strict OpenAI-compatible `tool_calls`.
   - **`aider_proxy.py` (For Aider)**:
     - **Strict Markdown Parser Fix**: Local LLMs often prepend language tags to code blocks (e.g., ```python). Aider's "Whole" parser crashes on this. The proxy actively strips these tags on the fly.
     - **"95% Freeze" Prevention**: Aider sometimes drops the connection if `finish_reason: stop` is sent in the same chunk as the final backticks. This proxy intentionally delays and separates the `finish_reason` into an empty chunk to ensure 100% successful code application.

2. **Monitor (`monitor/`)**: Watchdog daemon (`llama_monitor_daemon.py`) and TUI dashboard (`llama_monitor_tui.py`) to track VRAM usage, token generation speeds, and proxy intervention metrics in real-time.

3. **Scripts (`scripts/`)**: Developer utilities to instantly bootstrap standardized, agent-friendly development environments.
   - **[init-aider.sh](scripts/init-aider.sh)**: A generic bootstrap utility that initializes Git, creates virtual environments, installs linting/testing tools (e.g., `ruff`, `pytest`), and configures helper instructions.
   - **[init-aider-python.sh](scripts/init-aider-python.sh)**: A Python-specialized bootstrap tool. Generates standard config files (`pyproject.toml`, `pytest.ini`), sets up code-quality linters, and deploys specialized guidelines for UI frameworks like Textual or Rich.

### The Core Problems Solved
When running agents against local LLMs on resource-constrained hardware (like APUs), two major issues occur:
- **Prefill Collapse**: Even a 1-character change in the prompt invalidates the KV cache, forcing a massive, slow full recompute (Prefill).
- **Read Timeouts**: During long prefill calculations, the HTTP connection goes completely silent. Clients interpret this as a crashed server and drop the connection.

Both proxies utilize **Heartbeat Streaming** (sending empty SSE chunks every 60 seconds) to trick clients into waiting indefinitely. Additionally, `kilo_proxy.py` integrates with **Headroom** for intelligent context compression to prevent Out-of-Memory (OOM) errors (Aider handles its own context summarization natively).

### Installation
First, clone the repository and install the required Python dependencies:
```bash
git clone https://github.com/akkyey/llm-toolbox.git
cd llm-toolbox
pip install -r requirements.txt
```

### Usage (Systemd Service)
This is an experimental tool heavily optimized for specific workflows. Adjust the rules and endpoints according to your local LLM setup.

We use systemd template units to manage the proxies and the monitor.
1. Open the service files in `systemd/` and edit the placeholders (e.g., `YOUR_USERNAME`, `/path/to/...`) to match your environment.
2. Copy the service files and enable them:
```bash
sudo cp systemd/llm-proxy@.service /etc/systemd/system/
sudo cp systemd/stateforge-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llm-proxy@kilo
sudo systemctl enable --now llm-proxy@aider
sudo systemctl enable --now stateforge-monitor
```
3. Point your agent's API base URL to the respective proxy port:
   - Kilo Code (Cline): `http://localhost:9091/v1`
   - Aider: `http://localhost:9092/v1`
   The proxy will forward sanitized requests to your underlying llama-server on port `9090`.

### Viewing Logs
The proxy and monitor logs are managed by systemd's journal. You can view them in real-time using:
```bash
journalctl -u llm-proxy@kilo -f
journalctl -u llm-proxy@aider -f
journalctl -u stateforge-monitor -f
```
For `kilo_proxy`, the raw request payloads are also dumped as JSON files to `/var/log/kilo_proxy` (as defined in the systemd service) for debugging cache invalidation issues.

### Related Article
The full architectural breakdown, development story, and detailed mechanisms of these proxies are documented in our tech blog article:  
[Building a Dedicated Proxy for Local LLM Agents - Zenn](https://zenn.dev/akkyey/articles/abf10f9eb05f6a?locale=en)

---

## 日本語

本リポジトリは、ローカルLLM（llama.cpp等）環境で自律型AIコーディングエージェント（AiderやCline等）をシームレスかつ安定して稼働させ、監視するための統合ツールキットです。

### 構成コンポーネントとその違い
エージェントごとにLLMとの通信手法やパーサーの癖が異なるため、本リポジトリではそれぞれの仕様に特化したコンポーネントを用意しています。

1. **Proxy (`proxy/`)**: 特定のクライアント向けに最適化されたペイロードルーター。
   - **`kilo_proxy.py` (Cline / Kilo Code 用)**:
     - **動的プレフィックスのマスク**: Clineは毎回の通信で「現在時刻」や「巨大なファイルツリー」を送信します。これをダミー文字に強制置換することでKVキャッシュを保護し、2回目以降の推論を超高速化します。
     - **推論の枝刈り (Thought Pruning)**: 小型モデル特有の「長々とした言い訳ループ」を検知し、`[Reasoning Truncated]` と強制置換してコンテキストのノイズを排除します。
     - **ツールコール矯正**: LLMが誤って出力したXMLタグや構文エラーを、正規の `tool_calls` フォーマットに力技で補正します。
   - **`aider_proxy.py` (Aider 用)**:
     - **マークダウンタグの除去**: ローカルLLMはコードブロックに ```python のように言語名を付けがちですが、AiderのWholeパーサーはこれを許容しません。プロキシ側でこの言語名を正規表現で削ぎ落とします。
     - **「95%フリーズ問題」の回避**: Aiderは最後のテキストチャンクと同時に `finish_reason: stop` を受信すると、末尾数文字のバッファを切り捨ててパースに失敗することがあります。これを防ぐため、テキストを送り切った後に完全に独立した空チャンクで `finish_reason` のみを送信します。

2. **Monitor (`monitor/`)**: VRAM使用量、トークン生成速度、プロキシの介入指標をリアルタイムで追跡する監視用デーモン（`llama_monitor_daemon.py`）および TUI ダッシュボード（`llama_monitor_tui.py`）です。

3. **Scripts (`scripts/`)**: AIエージェントでの開発に適した標準化された開発環境を瞬時に構築するためのブートストラップスクリプト群です。
   - **[init-aider.sh](scripts/init-aider.sh)**: Gitの初期化、仮想環境の作成、テスティング/静的解析ツール（`ruff`、`pytest`など）の導入、エージェント向け指示書の自動配置を行う汎用セットアップユーティリティです。
   - **[init-aider-python.sh](scripts/init-aider-python.sh)**: Python開発に特化したセットアップユーティリティです。標準設定ファイル（`pyproject.toml`、`pytest.ini`）の自動生成、コード品質向上のためのLinter設定、TextualやRichなどのUIフレームワークに特化したガイドラインの配置を行います。

### 共通で解決する課題
限られたリソース（APUなど）のローカル環境でエージェントを動かすと、以下の問題が発生します：
- **プレフィル崩壊**: プロンプトが1文字でも変化するとKVキャッシュが破棄され、数十分もの長いプレフィル（再計算）が発生してしまいます。
- **タイムアウト切断**: 長いプレフィル計算中の沈黙を、クライアント側が「サーバーハング」と誤検知して接続を切断してしまいます。

両プロキシとも、計算中に60秒間隔で空のチャンクを送信する **ハートビート ストリーミング** を実装してタイムアウトを防ぎます。さらに `kilo_proxy.py` では、OSSの **Headroom** と連携してエラーログなどをインテリジェントに圧縮し、VRAM溢れ（OOM）を未然に防いでいます（Aider環境ではAider自身の自動要約機能にコンテキスト管理を委ねています）。

### インストール
まずリポジトリをクローンし、必要な依存パッケージをインストールします：
```bash
git clone https://github.com/akkyey/llm-toolbox.git
cd llm-toolbox
pip install -r requirements.txt
```

### 使い方 (Systemd サービス)
※特定の環境に特化して最適化された実験的なツールです。ご自身のローカルLLM環境に合わせて調整してご使用ください。

systemd テンプレートユニットを使用してプロキシとモニターを管理します。
1. `systemd/` 内のサービスファイルをテキストエディタで開き、プレースホルダー（`YOUR_USERNAME`、パス等）をご自身の環境に合わせて書き換えます。
2. サービスファイルを配置し、起動します：
```bash
sudo cp systemd/llm-proxy@.service /etc/systemd/system/
sudo cp systemd/stateforge-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llm-proxy@kilo
sudo systemctl enable --now llm-proxy@aider
sudo systemctl enable --now stateforge-monitor
```
3. エージェントの API Base URL をそれぞれのプロキシポートに向けます：
   - Kilo Code (Cline) 用: `http://localhost:9091/v1`
   - Aider 用: `http://localhost:9092/v1`
   プロキシがリクエストを無菌化し、バックエンドの llama-server (ポート9090) へ転送します。

### ログの確認方法
プロキシおよびモニターの動作ログは systemd の journal に出力されます。リアルタイムで監視するには以下のコマンドを使用します：
```bash
journalctl -u llm-proxy@kilo -f
journalctl -u llm-proxy@aider -f
journalctl -u stateforge-monitor -f
```
なお、`kilo_proxy` は KVキャッシュ破壊 of 調査用として、Kilo Codeからの巨大な生ペイロードを `/var/log/kilo_proxy` 配下にJSONファイルとしてダンプします。

### 関連記事
これらのプロキシのアーキテクチャや開発の裏話については、技術ブログにて詳細に解説しています：  
[定年退職して暇なのでローカルLLMエージェント用の専用プロキシを構築してみた - Zenn](https://zenn.dev/akkyey/articles/abf10f9eb05f6a)
