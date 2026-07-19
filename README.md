# Local LLM Toolbox (StateForge)

A unified toolkit for running, routing, and monitoring local LLMs (like llama.cpp) seamlessly with AI coding agents like Aider.  
Aider などの AI コーディングエージェントとローカル LLM（llama.cpp など）をシームレスに連携させ、実行・ルーティング・監視するための統合ツールキットです。

## Components / コンポーネント
1. **Proxy (`proxy/`)**: Intelligent payload routers (`aider_proxy.py`, `kilo_proxy.py`) that handle context compression, prompt rewriting, and error recovery for local models.  
   ローカルモデル向けの文脈圧縮、プロンプト書き換え、エラーリカバリを処理するインテリジェントなペイロードルーターです。
2. **Monitor (`monitor/`)**: Watchdog daemon and TUI dashboard to track VRAM usage, token generation speeds, and proxy intervention metrics in real-time.  
   VRAM 使用量、トークン生成速度、プロキシの介入指標をリアルタイムで追跡する監視用デーモンおよび TUI ダッシュボードです。
3. **Scripts (`scripts/`)**: Developer utilities, including `init-aider.sh` for instantly bootstrapping perfect Aider environments with linters and conventions.  
   Linter や開発規約を備えた最適な Aider 開発環境を瞬時に構築するための、`init-aider.sh` を含む開発者向けユーティリティ群です。

## Scripts (`scripts/`) / スクリプト
A set of bootstrapping scripts to instantly set up standardized, agent-friendly development environments (especially for Aider).  
AI エージェント（特に Aider）での開発に適した標準化された開発環境を瞬時に構築するためのブートストラップスクリプト群です。

- **[init-aider.sh](scripts/init-aider.sh)**: A generic bootstrap utility that initializes Git, creates virtual environments, installs linting/testing tools (e.g., `ruff`, `pytest`), and configures helper instructions.  
  Git の初期化、仮想環境の作成、テスティング/静的解析ツール（`ruff`、`pytest`など）の導入、エージェント向け指示書の自動配置を行う汎用セットアップユーティリティです。
- **[init-aider-python.sh](scripts/init-aider-python.sh)**: A Python-specialized bootstrap tool. Generates standard config files (`pyproject.toml`, `pytest.ini`), sets up code-quality linters, and deploys specialized guidelines for UI frameworks like Textual or Rich.  
  Python 開発に特化したセットアップユーティリティです。標準設定ファイル（`pyproject.toml`、`pytest.ini`）の自動生成、コード品質向上のための Linter 設定、Textual や Rich などの UI フレームワークに特化したガイドラインの配置を行います。

## Deployment / デプロイ
See `systemd/` for service files to run the Proxy and Monitor as background daemons.  
Proxy および Monitor をバックグラウンドデーモンとして動作させるためのサービスファイルについては、`systemd/` ディレクトリを参照してください。
