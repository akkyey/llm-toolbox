#!/usr/bin/env bash
set -euo pipefail

DIR="."
FRAMEWORK=""

DUAL_MODE=false

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [DIRECTORY]

Initialize an Aider Python environment in the specified DIRECTORY (default: current directory).

Options:
  -h, --help            Show this help message and exit
  -f, --framework NAME  Specify a framework for specific guidelines (Supported: textual, rich)
  --dual                Enable dual model mode (experimental)
EOF
}

# 引数のパース
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        --framework|-f)
            if [[ "$#" -lt 2 ]]; then
                echo "Error: --framework requires an argument."
                exit 1
            fi
            FRAMEWORK="$2"
            shift
            ;;
        --dual)
            DUAL_MODE=true
            ;;
        *)
            DIR="$1"
            ;;
    esac
    shift
done

# 未知のフレームワーク名に対する警告
if [ -n "$FRAMEWORK" ] && [ "$FRAMEWORK" != "textual" ] && [ "$FRAMEWORK" != "rich" ]; then
    echo "Warning: Unknown framework '$FRAMEWORK'. Supported: textual, rich"
    echo "Framework-specific setup will be skipped."
fi

# 安全対策：ディレクトリが存在し、かつ空でない（隠しファイル含む）場合はエラーにして終了
if [ -d "$DIR" ]; then
    if [ "$(ls -A "$DIR")" ]; then
        echo "Error: Directory '$DIR' already exists and is not empty."
        echo "Operation aborted to prevent accidental modifications."
        exit 1
    fi
else
    mkdir -p "$DIR"
    echo "Created directory: $DIR"
fi

cd "$DIR" || { echo "Failed to cd into $DIR"; exit 1; }

echo "Initializing Aider Python environment in $(pwd) ..."

# Gitリポジトリの初期化
git init
echo "Initialized empty Git repository."
mkdir -p .aider tests

# Python仮想環境の構築
if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "Created Python virtual environment in .venv/"
else
    echo "Virtual environment .venv already exists. Skipping."
fi

echo "Installing pytest, pytest-mock, pytest-cov, and ruff into .venv..."
.venv/bin/pip install --quiet pytest pytest-mock pytest-cov ruff || {
    echo "Error: pip install failed for base dependencies."; exit 1;
}

if [ ! -f pytest.ini ]; then
cat << 'EOF' > pytest.ini
[pytest]
pythonpath = .
EOF
    echo "Created pytest.ini with pythonpath = ."
fi

# pyproject.toml の生成（複雑度判定など）
if [ ! -f pyproject.toml ]; then
cat << 'EOF' > pyproject.toml
[tool.ruff]
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "C90", "B"]

[tool.ruff.lint.mccabe]
max-complexity = 15
EOF
    echo "Created pyproject.toml with C901 (McCabe complexity <= 15) and Bugbear."
fi

# フレームワーク固有のセットアップ
GUIDELINE_FILE=""
if [ "$FRAMEWORK" = "textual" ]; then
    echo "Installing Textual dependencies..."
    .venv/bin/pip install --quiet textual pytest-asyncio || {
        echo "Error: pip install failed for Textual dependencies."; exit 1;
    }
    
    cat << 'EOF' > .aider/textual_test_guideline.md
# Textual UI Test Guidelines

When testing the actual screen rendering and key inputs of a Textual app, do not use simple Mocks. Instead, use Textual's official asynchronous testing features (`run_test()` and `Pilot`).
**Note: In principle, do not use snapshot tests (syrupy, pytest-textual-snapshot). Perform deterministic tests (DOM validation) by querying widgets directly.**

## Basic Structure
1. Always add `@pytest.mark.asyncio` to the test function.
2. Launch the app using `async with app.run_test() as pilot:`.
3. Simulate user interactions with `pilot.press("enter")`, `pilot.click()`, etc.
4. Retrieve DOM elements with `pilot.app.query_one()` and directly assert their states or values.
EOF
    echo "Generated textual_test_guideline.md"
    GUIDELINE_FILE=".aider/textual_test_guideline.md"
elif [ "$FRAMEWORK" = "rich" ]; then
    echo "Installing Rich dependencies..."
    .venv/bin/pip install --quiet rich || {
        echo "Error: pip install failed for Rich dependencies."; exit 1;
    }
    
    cat << 'EOF' > .aider/rich_guideline.md
# Rich Terminal UI Implementation Guidelines

When building a TUI, use `rich.layout.Layout` and `rich.live.Live` to construct the screen.
Do not use other frameworks like Textual.

## 1. Basic Structure
* Divide the screen using `Layout` (`split_column`, `split_row`).
* Place Rich Renderable objects (like Panel or Table) in each layout area.
* Loop inside the context manager of `Live(layout, refresh_per_second=4)`, periodically update the data, and let the screen re-render.

## 2. Preventing Flicker and Layout Collapse
**Enable Alternative Screen:**
To prevent screen flickering and avoid cluttering the original terminal history after the application exits, you must enable the alternative screen by setting `Live(..., screen=True)`.

**Expand Table Width:**
When placing a `Table` inside a `Layout` area, it only expands to the text width by default, leaving unnatural whitespace on the right. If you want the table to fill the entire panel, you must set `Table(..., expand=True)`.

## 3. Layout Design and Size Management Rules
When dividing the screen vertically or horizontally using `rich.layout.Layout`, incorrect size specifications can cause panels to stretch vertically (creating empty voids) or cause text truncation. To prevent this, strictly follow these three rules.

**Rule 1: Specify `size` for fixed areas, keep parent layouts flexible**
* Child Layouts (Components): For layouts containing fixed-row content like headers, footers, or status bars, NEVER use `ratio`. Always explicitly specify the height using `size=number_of_lines` (e.g., `Layout(name="header", size=3)`).
* Parent Layouts (Containers): As a general rule, do not specify `size` for the top-level parent layout or outer frames; allow them to automatically follow (flex with) the terminal's overall size. This prevents layout collapse or crashes when the user resizes the window.

**Rule 2: Adjust whitespace with spacers**
If the entire screen consists of fixed-size components (using `size`), and unnatural whitespace appears at the bottom, append an empty spacer `Layout(name="spacer")` at the end of the column (do not specify `size` or `ratio` for this spacer). The spacer will absorb the extra height and prevent panels from stretching.
*Note: If there is a dynamic area (using `ratio`) in the screen, it will automatically absorb all whitespace, making spacers unnecessary.*

**Rule 3: Optimize dynamic content for screen size and slice data**
Allocate `ratio` to the main areas you want to stretch according to the data volume (e.g., logs, process lists, notification history) to utilize the remaining space.

**[CRITICAL] Dynamic Slicing on Data Retrieval:**
Rich's `Layout` does not provide an automatic scrollbar by default. If data exceeds the area's height (number of rows), it will be pushed off-screen and disappear, or worse, destroy the layout.
Therefore, instead of fixing the limit (e.g., "maximum 50 items uniformly"), you MUST implement logic to dynamically slice the data to fit the screen based on the current Layout's available lines (or `console.height`) (e.g., `logs[-max_lines:]`) before passing it to the renderable object.

## 4. Implementation and Testing Pitfalls (Anti-patterns)
**[STRICTLY PROHIBITED] Using the `in` operator on Layout objects, and direct iteration (`for` loops)**

* **BAD:**
  * `if "header" in layout:` or `assert "header" in layout`
  * `[sub.name for sub in layout]` (attempting direct iteration)
* **Error Produced:** `KeyError: 'No layout with name 0'`

**⚠️ Note (Architectural Reason)**
Rich's `Layout` class does not support the `in` operator (`__contains__` method) or standard iteration (`__iter__` method).
Therefore, Python automatically attempts to fetch elements starting from index `0` (`layout[0]`). However, the `Layout` class interprets `[0]` as "fetch the layout named `'0'`", leading to a mysterious `KeyError` crash.
This is a very common hallucination (misunderstanding of specs) in AI code generation, so please check strictly during reviews.

**Correct validation/testing methods:**
If you want to check if a specific layout exists, use one of the following approaches:

```python
# Solution A: Attempt direct access with try-except
try:
    header_layout = layout["header"]
except KeyError:
    # Handle absence (e.g., pytest.fail("...") in pytest)

# Solution B: Generate a list of child names using list comprehension and compare
child_names = [child.name for child in layout.children]
if "header" in child_names:
    # Handle presence
```

## 5. TUI Testing Strategy and Coverage
Infinite loops in TUIs (like `Live` or `while True`) cause freezes during testing. You must clearly separate the minimal startup code containing the loop (just a few lines) from the pure logic functions that build and update the UI (like layout creation or string generation).
UI building functions MUST be testable with \`pytest\`. You are only permitted to add \`# pragma: no cover\` to the genuinely untestable infinite loop lines (a few lines). Evading tests for entire functions is strictly prohibited.
EOF
    echo "Generated rich_guideline.md"
    GUIDELINE_FILE=".aider/rich_guideline.md"
fi

# Lintスクリプトの生成
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
.venv/bin/ruff check --fix --unsafe-fixes "${PY_FILES[@]}" > /dev/null 2>&1

# 直せなかったエラーを検出してAiderに渡す（シンプル出力）
.venv/bin/ruff check --output-format=concise --ignore=E501 "${PY_FILES[@]}"
exit $?
EOF_LINT
    chmod +x .aider/lint.sh
    echo "Created smart lint.sh wrapper for Python using ruff."
fi

# テストスクリプトの生成 (スマートテスト)
if [ ! -f .aider/test.sh ]; then
    cat << 'EOF_TEST' > .aider/test.sh
#!/usr/bin/env bash

# プロジェクト内に1つでも .py ファイルが存在するかチェック（.venv等の隠しフォルダは除外）
if ! find . -type f -name "*.py" -not -path "*/\.*" -print -quit | grep -q .; then
    # .pyファイルがまだ存在しない場合（ドキュメント作成時など）はテストをスキップして成功とする
    exit 0
fi

# pythonファイルが存在する場合はカバレッジ付きテストを実行
.venv/bin/pytest --cov=. --cov-report=term-missing --cov-fail-under=80 --maxfail=1 --tb=short
exit $?
EOF_TEST
    chmod +x .aider/test.sh
    echo "Created smart test.sh wrapper for Python."
fi

# テスト規約の生成
if [ ! -f .aider/CONVENTIONS.md ]; then
cat << 'EOF' > .aider/CONVENTIONS.md
# Development Rules & Conventions

## Absolute Obligation of Simultaneous Testing (Guardrail)
When creating or modifying source code (new files, modules, or functions), you MUST **simultaneously create or update the corresponding test code (`test_*.py`) in the very same step.**
The approach of "writing the main code first and adding tests later" is PROHIBITED. If you only create the main code, the automated tests that run immediately after will result in coverage errors and block progress.
However, executing an `if __name__ == "__main__":` block at the end of a file can cause unintended script execution or freezes, so avoid direct testing of this block.
You must add a `# pragma: no cover` comment to any blocks that are exempt from testing to maintain the overall coverage (80% or higher) without writing useless tests.

## Iterative Development
Even if you are provided with specifications or requirements, **NEVER attempt to complete all the source code at once.**
Repeat the following cycle for each small feature unit or component:
1. Implement one small feature.
2. Write its test code (and make the test pass).
3. Stop working and ask the user for confirmation.

## Policy for Dependency Errors (Preventing Hallucinations)
If you encounter module import errors or undefined errors during test execution, do not blindly rewrite import paths.
The true cause is highly likely to be a syntax error inside the "imported" module. Check the error logs and fix the root cause.

## Python Environment and Command Execution
This project sets up a virtual environment in the `.venv` directory. When instructing the user to execute commands or proposing package installations, always use `.venv/bin/pip` or `.venv/bin/python` (e.g., write `.venv/bin/pip install ...` instead of `pip install ...`).

## Prohibition of Thinking Process (For Local Models)
NEVER use `<think>` tags. Do not perform internal chain-of-thought reasoning.
Provide the final answer and code edits immediately and directly.
EOF
    echo "Created .aider/CONVENTIONS.md"
fi

# Aider設定の生成
if [ ! -f .aider.conf.yml ]; then
    echo "test-cmd: ./.aider/test.sh" > .aider.conf.yml
    echo "auto-test: true" >> .aider.conf.yml
    echo "lint-cmd: ./.aider/lint.sh" >> .aider.conf.yml
    echo "auto-lint: false" >> .aider.conf.yml
    echo "auto-commits: false" >> .aider.conf.yml
    echo "suggest-shell-commands: false" >> .aider.conf.yml
    echo "yes-always: false" >> .aider.conf.yml
    echo "max-chat-history-tokens: 1000000" >> .aider.conf.yml
    echo "model-settings-file: .aider.model.settings.yml" >> .aider.conf.yml
    echo "model-metadata-file: .aider.model.metadata.json" >> .aider.conf.yml
    
    if [ -n "$GUIDELINE_FILE" ]; then
        echo "read: [$GUIDELINE_FILE, .aider/CONVENTIONS.md]" >> .aider.conf.yml
    else
        echo "read: [.aider/CONVENTIONS.md]" >> .aider.conf.yml
    fi
    
    if [ "$DUAL_MODE" = true ]; then
        echo "architect: true" >> .aider.conf.yml
        echo "model: openai/gemma4" >> .aider.conf.yml
        echo "editor-model: openai/qwen35b" >> .aider.conf.yml
    fi
    
    echo "Created .aider.conf.yml"
fi

# モデル設定の生成 (65k context limit override & diff format)
if [ ! -f .aider.model.metadata.json ]; then
cat << 'EOF' > .aider.model.metadata.json
{
  "openai/qwen2.5-coder-32b-instruct": {
    "max_input_tokens": 1000000,
    "max_chat_history_tokens": 1000000
  },
  "openai/qwen35b": {
    "max_input_tokens": 1000000,
    "max_chat_history_tokens": 1000000
  },
  "openai/gemma4": {
    "max_input_tokens": 1000000,
    "max_chat_history_tokens": 1000000
  }
}
EOF
fi

if [ ! -f .aider.model.settings.yml ]; then
cat << 'EOF' > .aider.model.settings.yml
- name: openai/qwen2.5-coder-32b-instruct
  edit_format: diff
- name: openai/qwen35b
  edit_format: diff
- name: openai/gemma4
  edit_format: diff
EOF
fi

# .gitignoreの生成
if [ ! -f .gitignore ]; then
    touch .gitignore
fi
for ignore in ".venv/" "__pycache__/" ".pytest_cache/" ".aider/" ".aider.conf.yml" ".aider.model.metadata.json" ".aider.model.settings.yml" ".aider.chat.history.md" ".aider.input.history" ".aider.tags.cache*" "coverage.out"; do
    if ! grep -Fxq -- "$ignore" .gitignore; then
        echo "$ignore" >> .gitignore
    fi
done

echo "Done! Python Environment is ready."
