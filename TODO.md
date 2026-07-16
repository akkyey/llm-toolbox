# プロジェクト全体・プロキシ改善バックログ (Backlog & TODO)

このドキュメントは、LLM Proxy（`aider_proxy`, `kilo_proxy`, `codex_proxy`）の安定性向上や機能改善に向けたバックログをまとめたものです。

## 🚀 プロキシ機能改善 (Proxy Improvements)

### 1. `llama-server` スロット消去のハードコード回避 (Slot Erasure Customization)
* **概要**:
  現在、[aider_proxy.py](file:///home/irom/dev/llm-toolbox/proxy/aider_proxy.py) 等の新セッション開始時処理において、キャッシュ消去のリクエスト先が `slots/0` とハードコードされています。
* **課題**:
  サーバーが `--parallel 2` 以上で起動されている場合や、スロット `0` 以外を使用している構成において、不適切なスロットクリーンアップが発生したり機能しない可能性があります。
* **対策**:
  スロット番号（`0`）を環境変数（例: `LLAMA_SLOT_INDEX`）や引数から指定できるように改善します。

### 2. ハルシネーション（言い訳）検知バッファ判定のロバスト化 (Robust Hallucination Detection)
* **概要**:
  現在、[aider_proxy.py](file:///home/irom/dev/llm-toolbox/proxy/aider_proxy.py) 内の `fetch_stream_with_hallucination_detection` において、アクセス権限がない旨の「言い訳」検出を `50 < len(content_buffer) < 600` の範囲に限定しています。
* **課題**:
  LLM が最初に長めの自己紹介や無関係なトークンを 600 文字以上出力した後に「アクセスできません」等のフレーズを出力した場合、検知をすり抜けてしまいます。
* **対策**:
  バッファ上限を拡張するか、バッファ全体ではなく「直近の数文字（スライディングウィンドウ）」に対して判定を行うようにアルゴリズムを改良します。

### 3. 本番用WSGIサーバーによる配信 (Production WSGI Server Deployment)
* **概要**:
  各プロキシは現在 Flask の開発サーバー (`app.run()`) で稼働しています。
* **対策**:
  ローカル運用が主ですが、より安定した動作を確保するため、`waitress` や `gunicorn` などの軽量WSGIサーバーを背後で動かせるように起動用スクリプトやエントリポイントを整備します。
