#!/usr/bin/env bash

DIR="."
LANG="python" # デフォルト

# 引数のパース
FRAMEWORK=""
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --lang|-l)
            LANG="$2"
            shift
            ;;
        --framework|-f)
            FRAMEWORK="$2"
            shift
            ;;
        *)
            DIR="$1"
            ;;
    esac
    shift
done

# 安全対策：ディレクトリが存在し、かつ空でない（隠しファイル含む）場合はエラーにして終了
if [ -d "$DIR" ]; then
    # ls -A は . と .. 以外のすべてのファイル・フォルダを表示します
    if [ "$(ls -A "$DIR")" ]; then
        echo "Error: Directory '$DIR' already exists and is not empty."
        echo "Operation aborted to prevent accidental modifications."
        exit 1
    fi
else
    # 存在しない場合は新規作成
    mkdir -p "$DIR"
    echo "Created directory: $DIR"
fi

cd "$DIR" || { echo "Failed to cd into $DIR"; exit 1; }

echo "Initializing Aider environment in $(pwd) (Language: $LANG) ..."

# Gitリポジトリの初期化
git init
echo "Initialized empty Git repository."
mkdir -p .aider

# 言語別のセットアップ
case "$LANG" in
    python)
        mkdir -p tests
        echo "Created tests/ directory."

        if [ ! -d .venv ]; then
            python3 -m venv .venv
            echo "Created Python virtual environment in .venv/"
        else
            echo "Virtual environment .venv already exists. Skipping."
        fi

        echo "Installing pytest, pytest-mock, pytest-cov, and ruff into .venv..."
        .venv/bin/pip install pytest pytest-mock pytest-cov ruff >/dev/null 2>&1
        
        if [ ! -f pytest.ini ]; then
cat << 'EOF' > pytest.ini
[pytest]
pythonpath = .
EOF
            echo "Created pytest.ini with pythonpath = ."
        else
            echo "pytest.ini already exists. Skipping."
        fi
        
        GUIDELINE_FILE=""
        if [ "$FRAMEWORK" = "textual" ]; then
            echo "Installing Textual test dependencies (textual, pytest-asyncio)..."
            .venv/bin/pip install textual pytest-asyncio >/dev/null 2>&1
            
            cat << 'EOF' > .aider/textual_test_guideline.md
# Textual UI テストの書き方ガイドライン

Textualアプリの実際の画面描画やキー入力をテストする際は、単なるMockではなく、Textual公式の非同期テスト機能（`run_test()` と `Pilot`）を使用してください。
**注意: スナップショットテスト（syrupy, pytest-textual-snapshot）は原則として使用せず、ウィジェットを直接クエリする決定論的テスト（DOM検証）を行ってください。**

## 基本的な構造

1. テスト関数には必ず `@pytest.mark.asyncio` を付与します。
2. `async with app.run_test() as pilot:` を使ってアプリを起動します。
3. `pilot.press("enter")` や `pilot.click()` などでユーザー操作をシミュレートします。
4. `pilot.app.query_one()` でDOM要素を取得し、その状態や値を直接アサートします。

## サンプルコード

```python
import pytest
from app import MyTextualApp # テスト対象のアプリ
from textual.widgets import Label, DataTable

@pytest.mark.asyncio
async def test_app_basic_flow():
    app = MyTextualApp()
    
    # run_test() でアプリを仮想ターミナルにマウントする
    async with app.run_test() as pilot:
        
        # 1. 初期状態のアサート（DOMを直接検査）
        header = pilot.app.query_one("#header-title", Label)
        assert header.renderable == "初期タイトル"
        
        # 2. ユーザー操作のシミュレーション
        await pilot.press("j")
        await pilot.press("t", "e", "s", "t", "enter")
        
        # 3. 操作後の状態変化をアサート（外部ファイルに依存しない決定論的テスト）
        list_view = pilot.app.query_one("#my-list", DataTable)
        assert list_view.row_count > 0
        
        # 画面のテキスト構造を確認したい場合は、該当ウィジェットを抽出して検査する
```
EOF
            echo "Generated textual_test_guideline.md"
            GUIDELINE_FILE=".aider/textual_test_guideline.md"
        elif [ "$FRAMEWORK" = "rich" ]; then
            echo "Installing Rich dependencies..."
            .venv/bin/pip install rich >/dev/null 2>&1
            
            cat << 'EOF' > .aider/rich_guideline.md
# Rich ターミナルUI 実装ガイドライン

TUIを構築する際は `rich.layout.Layout` と `rich.live.Live` を使用して画面を構成してください。
Textualなどの他のフレームワークは使用しないでください。

## 基本的な構造
1. `Layout` を使って画面を分割します（`split_column`, `split_row`）。
2. 各レイアウトにPanelやTableなどのRichのRenderableオブジェクトを配置します。
3. `Live(layout, refresh_per_second=4)` のコンテキストマネージャー内でループを回し、定期的にデータを更新してください。
EOF
            echo "Generated rich_guideline.md"
            GUIDELINE_FILE=".aider/rich_guideline.md"
        fi

        if [ ! -f .aider/lint.sh ]; then
            cat << 'EOF_LINT' > .aider/lint.sh
#!/usr/bin/env bash

PY_FILES=()
for arg in "$@"; do
    if [[ "$arg" == *.py ]]; then
        PY_FILES+=("${arg#./}")
    fi
done

if [ ${#PY_FILES[@]} -eq 0 ]; then
    exit 0
fi

# 機械（ツール）による自動修正を先に実行
.venv/bin/ruff format "${PY_FILES[@]}" > /dev/null 2>&1
.venv/bin/ruff check --fix "${PY_FILES[@]}" > /dev/null 2>&1

# 直せなかったエラーを検出してAiderに渡す
.venv/bin/ruff check "${PY_FILES[@]}"
exit $?
EOF_LINT
            chmod +x .aider/lint.sh
            echo "Created smart lint.sh wrapper for Python using ruff."
        fi

        if [ ! -f .aider.conf.yml ]; then
            echo "test-cmd: .venv/bin/pytest --cov=. --cov-report=term-missing --cov-fail-under=80 --maxfail=1 --tb=short" > .aider.conf.yml
            echo "auto-test: true" >> .aider.conf.yml
            echo "lint-cmd: ./.aider/lint.sh" >> .aider.conf.yml
            echo "auto-lint: true" >> .aider.conf.yml
            echo "model-settings-file: .aider.model.settings.yml" >> .aider.conf.yml
            if [ -n "$GUIDELINE_FILE" ]; then
                echo "read: [$GUIDELINE_FILE]" >> .aider.conf.yml
                echo "Created .aider.conf.yml with pytest coverage and $FRAMEWORK guidelines."
            else
                echo "Created .aider.conf.yml with pytest coverage settings (80% minimum)."
            fi
        else
            echo ".aider.conf.yml already exists. Skipping."
        fi
        ;;
    node|js|ts)
        mkdir -p tests
        echo "Created tests/ directory."
        
        if [ ! -f .aider.conf.yml ]; then
cat << 'EOF' > .aider.conf.yml
test-cmd: npm test
auto-test: true
EOF
            echo "Created .aider.conf.yml with npm test settings."
        else
            echo ".aider.conf.yml already exists. Skipping."
        fi
        ;;
    go)
        if [ ! -f go.mod ]; then
            # デフォルトモジュール名で初期化
            go mod init "myapp"
            echo "Initialized go.mod"
        fi

        if [ ! -f .aiderignore ]; then
            echo "go.sum" > .aiderignore
            echo "coverage.out" >> .aiderignore
            echo "Created .aiderignore to hide noisy files from Aider."
        fi


        # 複雑度・保守性メトリクスの設定 (Xenon/Radonの代替)
        if [ ! -f .golangci.yml ]; then
            cat << 'EOF' > .golangci.yml
linters-settings:
  gocyclo:
    min-complexity: 15
  gocognit:
    min-complexity: 15
  maintidx:
    under: 20

linters:
  enable:
    - gocyclo
    - gocognit
    - maintidx
EOF
            echo "Created .golangci.yml with complexity and maintainability metrics."
        fi

        GUIDELINE_FILE=""
        if [ "$FRAMEWORK" = "bubbletea" ]; then
            cat << 'EOF' > .aider/bubbletea_guideline.md
# Bubble Tea TUI 開発ガイドライン

Go言語のTUIフレームワーク `Bubble Tea` を使用した開発では、The Elm Architectureパターンを厳守してください。

1. **Model (状態)**: アプリの全状態は `Model` 構造体に集約します。
2. **Init**: 初期のコマンド（非同期処理の開始など）を返します。不要な場合は `nil`。
3. **Update**: `Update(msg tea.Msg)` のみが状態を更新できます。副作用は `tea.Cmd` として返します。
   - **【重要】missing return エラーの防止**: switch文を使う場合、分岐漏れによるコンパイルエラーが多発します。これを防ぐため、switch文の中の `default:` だけでなく、**必ず関数の一番最後（switch文を抜けた後）**に `return m, nil` を記述してください。
     ```go
     func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
         switch msg := msg.(type) {
         // ...
         }
         return m, nil // <-- 必ず関数の末尾にこれを書くこと！
     }
     ```
4. **View**: `View()` は純粋関数として副作用を持たず、Modelから描画用文字列を生成します。スタイリングには `lipgloss` を使用してください。
EOF
            echo "Generated bubbletea_guideline.md"
            GUIDELINE_FILE=".aider/bubbletea_guideline.md"
        fi

        if [ ! -f .aider/lint.sh ]; then
            cat << 'EOF_LINT' > .aider/lint.sh
#!/usr/bin/env bash

# Extract .go files from arguments
GO_FILES=()
for arg in "$@"; do
    if [[ "$arg" == *.go ]]; then
        # Normalize by removing leading ./
        clean_name="${arg#./}"
        GO_FILES+=("$clean_name")
    fi
done

# Skip linting if no .go files were provided
if [ ${#GO_FILES[@]} -eq 0 ]; then
    exit 0
fi

go mod tidy

# 機械（ツール）による自動修正を先に実行
go fmt ./... > /dev/null 2>&1
~/go/bin/golangci-lint run --fix > /dev/null 2>&1

# Run global lint and capture output
TMP_OUT=$(mktemp)
~/go/bin/golangci-lint run > "$TMP_OUT" 2>&1

# 重大なコンパイルエラー（typecheckやimportエラー）がある場合は、フィルタリングせずに全出力する
if grep -qE "typecheck|could not import|build failed" "$TMP_OUT"; then
    echo "🚨 ERROR: Global compilation failed during linting."
    echo "Aider / LLM Notice: DO NOT guess fixes blindly. The true cause is likely a syntax/compile error."
    grep -E "(typecheck|could not import|build failed)" "$TMP_OUT"
    rm -f "$TMP_OUT"
    exit 1
fi

# Filter output to ONLY show actual errors (file:line:col:) for the requested files
HAS_RELEVANT_ERROR=0
for file in "${GO_FILES[@]}"; do
    if grep -E "^(\./)?$file:[0-9]+:[0-9]+:" "$TMP_OUT"; then
        # ★エラー行を標準出力に出す（Aiderに読ませるため）
        grep -E "^(\./)?$file:[0-9]+:[0-9]+:" "$TMP_OUT"
        HAS_RELEVANT_ERROR=1
    fi
done

rm -f "$TMP_OUT"

if [ $HAS_RELEVANT_ERROR -eq 1 ]; then
    exit 1
else
    exit 0
fi
EOF_LINT
            chmod +x .aider/lint.sh
            echo "Created smart lint.sh wrapper for Go."
        fi

        if [ ! -f .aider/test.sh ]; then
            cat << 'EOFTEST' > .aider/test.sh
#!/usr/bin/env bash

# Skip testing if there are no .go files anywhere in the project
if ! find . -name "*.go" -print -quit | grep -q .; then
    exit 0
fi

# 1. 全パッケージの事前ビルドチェック（深部のコンパイルエラーを表面化させる）
go test -run=^$ ./...
BUILD_STATUS=$?
if [ $BUILD_STATUS -ne 0 ]; then
    echo "=================================================="
    echo "🚨 ERROR: Package compilation failed."
    echo "Aider / LLM Notice: If you see a 'could not import' error, DO NOT change the import paths blindly."
    echo "The true cause is likely a syntax/compile error INSIDE the imported package."
    echo "Run 'go test ./path/to/failing_package' directly to reveal the hidden compilation error."
    echo "=================================================="
    exit $BUILD_STATUS
fi

# 2. カバレッジ付きでテスト実行
go test -v -coverprofile=coverage.out ./...
TEST_STATUS=$?
if [ $TEST_STATUS -ne 0 ]; then exit $TEST_STATUS; fi
COVERAGE=$(go tool cover -func=coverage.out | grep total: | awk '{print $3}' | tr -d '%')
if [ -z "$COVERAGE" ]; then
    echo 'Could not determine coverage.'
    exit 1
fi
echo "Current Coverage: $COVERAGE%"
if (( $(echo "$COVERAGE < 80.0" | bc -l) )); then
    echo "Error: Test coverage $COVERAGE% is below the required threshold of 80%"
    exit 1
fi
exit 0
EOFTEST
            chmod +x .aider/test.sh
            echo "Created test.sh wrapper with 80% coverage check and build pre-check."
        fi

        if [ ! -f .aider.conf.yml ]; then
            echo "test-cmd: ./.aider/test.sh" > .aider.conf.yml
            echo "auto-test: true" >> .aider.conf.yml
            echo "lint-cmd: ./.aider/lint.sh" >> .aider.conf.yml
            echo "auto-lint: true" >> .aider.conf.yml
            
            if [ -n "$GUIDELINE_FILE" ]; then
                echo "read: [$GUIDELINE_FILE]" >> .aider.conf.yml
                echo "Created .aider.conf.yml with go test/lint and $FRAMEWORK guidelines."
            else
                echo "Created .aider.conf.yml with go test and linting settings."
            fi
        else
            echo ".aider.conf.yml already exists. Skipping."
        fi
        ;;
    rust|rs)
        if [ ! -f Cargo.toml ]; then
            # Cargo.tomlがなければ初期化
            cargo init
            echo "Initialized Rust project with cargo init."
        fi

        if [ ! -f .aider/lint.sh ]; then
            cat << 'EOF_LINT' > .aider/lint.sh
#!/usr/bin/env bash

# 先に自動フォーマットと安全な自動修正を試みる
cargo fmt > /dev/null 2>&1
cargo clippy --fix --allow-dirty --allow-no-vcs > /dev/null 2>&1

# global lint を実行
TMP_OUT=$(mktemp)
cargo clippy --message-format=short > "$TMP_OUT" 2>&1
LINT_EXIT_CODE=$?

# 重大なコンパイルエラーがある場合は抽出してLLMに提示
if grep -q "could not compile" "$TMP_OUT"; then
    echo "🚨 ERROR: Global compilation failed during linting."
    echo "Aider / LLM Notice: DO NOT guess fixes blindly. The true cause is likely a syntax/compile error."
    grep -E "(error|could not compile)" "$TMP_OUT"
    rm -f "$TMP_OUT"
    exit 1
fi

if [ $LINT_EXIT_CODE -ne 0 ]; then
    cat "$TMP_OUT"
    rm -f "$TMP_OUT"
    exit 1
fi

rm -f "$TMP_OUT"
exit 0
EOF_LINT
            chmod +x .aider/lint.sh
            echo "Created smart lint.sh wrapper for Rust."
        fi

        if [ ! -f .aider/test.sh ]; then
            cat << 'EOFTEST' > .aider/test.sh
#!/usr/bin/env bash

# 1. 事前ビルドチェック（コンパイルエラーを表面化させる）
cargo check --tests
BUILD_STATUS=$?
if [ $BUILD_STATUS -ne 0 ]; then
    echo "=================================================="
    echo "🚨 ERROR: Project compilation failed."
    echo "Aider / LLM Notice: DO NOT hallucinate test fixes. The true cause is a syntax/compile error."
    echo "Read the compiler output carefully before modifying anything."
    echo "=================================================="
    exit $BUILD_STATUS
fi

# 2. テスト実行
cargo test
TEST_STATUS=$?
if [ $TEST_STATUS -ne 0 ]; then exit $TEST_STATUS; fi
exit 0
EOFTEST
            chmod +x .aider/test.sh
            echo "Created test.sh wrapper for Rust."
        fi

        if [ ! -f .aider.conf.yml ]; then
            echo "test-cmd: ./.aider/test.sh" > .aider.conf.yml
            echo "auto-test: true" >> .aider.conf.yml
            echo "lint-cmd: ./.aider/lint.sh" >> .aider.conf.yml
            echo "auto-lint: true" >> .aider.conf.yml
            echo "Created .aider.conf.yml with cargo test and clippy settings."
        else
            echo ".aider.conf.yml already exists. Skipping."
        fi
        ;;
    *)
        echo "Warning: Unrecognized language '$LANG'. Creating generic Aider config."
        if [ ! -f .aider.conf.yml ]; then
cat << 'EOF' > .aider.conf.yml
auto-test: true
EOF
            echo "Created basic .aider.conf.yml."
        else
            echo ".aider.conf.yml already exists. Skipping."
        fi
        ;;
esac


# テスト規約の生成 (全言語共通)
if [ ! -f .aider/CONVENTIONS.md ]; then
cat << 'EOF' > .aider/CONVENTIONS.md
# 開発ルール・規約 (CONVENTIONS)

## テストの必須化
新しい機能、パッケージ、または関数を実装・変更した際は、**必ず**対応するテストコード（_test.go, test_*.py など）を作成・更新してください。
本体の実装だけで終わらせず、テストを実行してカバレッジを通すところまでを1つのステップとしてください。

## 段階的・反復的な実装（Iterative Development）
仕様書や要件定義を渡された場合でも、**絶対に一度にすべてのソースコードを完成させようとしないでください。**
1つのコンポーネントや小さな機能単位（1パッケージや1モジュール）ごとに、以下のサイクルを回してください：
1. 1つの小さな機能を実装する
2. そのテストコードを書く（そしてテストをパスさせる）
3. 作業を一旦止め、ユーザーに「次は〇〇を実装しますがよろしいですか？」と確認を求める
この「少しずつ作って確認する」ステップを厳密に守ってください。

## 依存ライブラリの追加について（Go言語など）
go.mod などの依存関係ファイルを手動で編集して、ライブラリのバージョン番号を推測・直書きすることは絶対に避けてください。（存在しないバージョンを指定してしまい、ビルド不能になるのを防ぐためです）
新しいライブラリを追加する際は、必ずターミナルコマンド（例: `go get github.com/...@latest`）を実行して、ツールキット側に正しい最新バージョンを解決させてください。

## 依存パッケージのインポート・ビルドエラー時の対応方針（ハルシネーション防止）
テスト実行時に `could not import myapp/...` やモジュールのビルドエラーに遭遇した場合、エラーが発生したテストファイル（例: `integration_test.go`）のインポートパスやパッケージ名を無闇に書き換えないでください。
エラーの真の原因は「インポートされた側（依存パッケージ）の内部での構文エラーや未定義エラー」である可能性が高いです（標準のテストログではこの本当の原因が省略されがちです）。
この場合、ハルシネーションを起こす前に、以下の手順を踏んで「本当の原因」を特定してください：
1. エラーメッセージで指摘されている依存パッケージ単体を対象にテストまたはビルドを実行する（例: Goなら `go test ./internal/model`）。
2. そこで出力された「本当のエラーログ」を確認し、原因となっているパッケージ内部のソースコードを読み込んで根本原因を修正する。

## カバレッジ向上と main() 関数のリファクタリング
テストカバレッジが目標値に届かない場合、無闇にテストケースを自己増殖させないでください。特に `main()` 関数はテストフレームワークから直接呼び出しにくいため、カバレッジ低下の原因になりがちです。
その場合は、`main()` 関数内のロジック（設定の読み込みやアプリの起動処理など）を別のテスト可能な関数（例: `run() error` や `StartApp()`）に切り出し、`main()` はそれを呼び出して終了するだけ（`os.Exit`）の極薄なラッパーにリファクタリングしてください。
EOF
    echo "Created .aider/CONVENTIONS.md (Test rules)."
fi

# .aider.conf.yml に .aider/CONVENTIONS.md を追加
if [ -f .aider.conf.yml ]; then
    if grep -q "^read:" .aider.conf.yml; then
        if ! grep -q ".aider/CONVENTIONS.md" .aider.conf.yml; then
            sed -i 's|^read: \[|read: [.aider/CONVENTIONS.md, |' .aider.conf.yml
        fi
    else
        echo "read: [.aider/CONVENTIONS.md]" >> .aider.conf.yml
    fi
fi

# Aiderのコンテキスト制限解除（全言語共通）
if [ ! -f .aider.model.metadata.json ]; then
cat << 'EOF' > .aider.model.metadata.json
{
  "openai/qwen2.5-coder-32b-instruct": {
    "max_input_tokens": 65536,
    "max_chat_history_tokens": 32768
  }
}
EOF
    echo "Created .aider.model.metadata.json (65k context for Qwen2.5-Coder)."
fi

# edit_format の明示的指定（YAML）
if [ ! -f .aider.model.settings.yml ]; then
cat << 'EOF' > .aider.model.settings.yml
- name: openai/qwen2.5-coder-32b-instruct
  edit_format: diff
EOF
    echo "Created .aider.model.settings.yml (diff format)."
fi

if ! grep -q "model-settings-file" .aider.conf.yml; then
    echo "model-settings-file: .aider.model.settings.yml" >> .aider.conf.yml
    echo "Appended model-settings-file to .aider.conf.yml."
fi
echo "Done! Environment is ready."

if [ ! -f .gitignore ]; then
    touch .gitignore
fi
for ignore in ".aider/" ".aider.conf.yml" ".aider.model.metadata.json" ".aider.model.settings.yml" ".aider.chat.history.md" ".aider.input.history" ".aider.tags.cache.v4" ".golangci.yml" "coverage.out"; do
    if ! grep -q "^$ignore$" .gitignore; then
        echo "$ignore" >> .gitignore
    fi
done
