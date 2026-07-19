#!/usr/bin/env python3
"""
Aider Proxy 用の共通モジュール
"""

import glob
import time
import json
import logging
import re
import os
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

def send_dummy_chunks(content: str, res: Dict[str, Any], finish_reason: Optional[str] = None, tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
    """
    Aiderのパーサーが正常に動作するよう、擬似ストリーミング（小分け送信）を行う

    Args:
        content: 送信するコンテンツ
        res: レスポンス情報
        finish_reason: 終了理由
        tool_calls: ツール呼び出し情報
    """
    # Aiderのパーサーが正常に動作するよう、擬似ストリーミング（小分け送信）を行う
    chunk_size = 40
    delay = 0.01  # 秒

    for i in range(0, len(content), chunk_size):
        chunk_text = content[i:i+chunk_size]
        chunk_data = {
            "id": res.get("id", "chatcmpl-aider-proxy-resp"),
            "object": "chat.completion.chunk",
            "created": res.get("created", int(time.time())),
            "model": res.get("model", "qwen"),
            "choices": [{
                "index": 0,
                "delta": {
                    "content": chunk_text
                },
                "finish_reason": None
            }]
        }
        yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
        time.sleep(delay)

    # テキスト送信完了後、最後の空チャンクで finish_reason: "stop" (または元々の finish_reason) を送る
    # これによりAiderが最後の閉じバッククォートをバッファで切り捨てるのを防ぐ
    last_chunk = {
        "id": res.get("id", "chatcmpl-aider-proxy-resp"),
        "object": "chat.completion.chunk",
        "created": res.get("created", int(time.time())),
        "model": res.get("model", "qwen"),
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason
        }]
    }
    if tool_calls:
        last_chunk["choices"][0]["delta"]["tool_calls"] = tool_calls

    yield f"data: {json.dumps(last_chunk, ensure_ascii=False)}\n\n"
    time.sleep(delay)

def detect_expected_fence(data: Dict[str, Any]) -> str:
    """
    Aiderの期待するフェンス（バッククォートの数）を検出

    Args:
        data: リクエストデータ

    Returns:
        Aiderの期待するフェンス文字列
    """
    expected_fence = "```"
    for msg in data.get('messages', []):
        if msg.get('role') == 'system':
            sys_content = msg.get('content', '')
            # "path/to/filename.js" の直後にあるバッククォートを検出
            match = re.search(r'path/to/filename\.[a-zA-Z0-9]+\s+(`{3,5})', sys_content)
            if match:
                expected_fence = match.group(1)
                logger.info(f"[FENCE DETECT] Aider expects fence: {expected_fence}")
                break
    return expected_fence

def standardize_fences(content: str, expected_fence: str) -> str:
    """
    レスポンス内のフェンス（バッククォート3〜5個、言語名オプション）をAiderの期待するフェンスに統一

    Args:
        content: レスポンス内容
        expected_fence: Aiderの期待するフェンス文字列

    Returns:
        統一された内容
    """
    # レスポンス内のフェンス（バッククォート3〜5個、言語名オプション）をAiderの期待するフェンスに統一
    # 例: ```python や ```` などをすべて Aiderが期待する expected_fence に置換
    replaced_content = re.sub(r'^\s*`{3,5}[a-zA-Z0-9_-]*\s*$', expected_fence, content, flags=re.MULTILINE)
    if replaced_content != content:
        logger.info(f"[FILTER] Standardized fences to match Aider expectations ({expected_fence})")
        content = replaced_content
    return content

def save_aider_log(data: Dict[str, Any], original_response: str, filtered_response: str, log_dir: Optional[str] = None) -> None:
    """
    Aiderのログをファイルに保存

    Args:
        data: リクエストデータ
        original_response: 元のレスポンス
        filtered_response: フィルタリング後のレスポンス
        log_dir: ログ出力先ディレクトリ（省略時は環境変数 AIDER_PROXY_LOG_DIR を参照）
    """
    if log_dir is None:
        log_dir = os.environ.get("AIDER_PROXY_LOG_DIR", os.path.join(os.getcwd(), "aider_logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"chat_log_{int(time.time())}.json")
    failure_reason = None
    messages = data.get("messages", [])
    if messages and isinstance(messages, list):
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, str):
            if "did not find a match for that SEARCH block" in last_msg:
                failure_reason = "FORMAT_ERROR_SEARCH_MISMATCH"
            elif "Bad/missing filename" in last_msg or "Invalid edit block" in last_msg:
                failure_reason = "FORMAT_ERROR_FILENAME"
            elif "You must use the" in last_msg and "block to output your edits" in last_msg:
                failure_reason = "FORMAT_ERROR_FENCE"
            elif "I am an AI" in original_response or "申し訳" in original_response:
                failure_reason = "ROLE_CONFUSION_CHATBOT"
            elif "Fail Fast:" in filtered_response:
                if "exceeded_15000_tokens_limit" in filtered_response:
                    failure_reason = "EXCEEDED_15000_TOKENS_LIMIT"
                elif "repetition_loop" in filtered_response:
                    failure_reason = "REPETITION_LOOP"
                elif "chatbot_phrase" in filtered_response:
                    failure_reason = "CHATBOT_PHRASE"
                elif "SEARCH block" in last_msg:
                    failure_reason = "FORMAT_ERROR_SEARCH_MISMATCH_FAILFAST"
                elif "filename" in last_msg:
                    failure_reason = "FORMAT_ERROR_FILENAME_FAILFAST"
                else:
                    failure_reason = "FAILFAST_OTHER"

            # Check if the matched file was actually missing from the context
            is_mismatch = failure_reason in ["FORMAT_ERROR_SEARCH_MISMATCH", "FORMAT_ERROR_SEARCH_MISMATCH_FAILFAST"]
            is_potential_null_fail = failure_reason is None and "<<<<<<< SEARCH" in (original_response or "")
            
            if is_mismatch or is_potential_null_fail:
                if _is_target_file_missing_from_context(original_response, messages):
                    if failure_reason == "FORMAT_ERROR_SEARCH_MISMATCH_FAILFAST" or "Fail Fast:" in (filtered_response or ""):
                        failure_reason = "CONTEXT_MISSING_FILE_FAILFAST"
                    else:
                        failure_reason = "CONTEXT_MISSING_FILE"

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "failure_reason": failure_reason,
                "request": data,
                "original_response": original_response or "",
                "filtered_response": filtered_response
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"[LOG DUMP] Saved to {log_file}")
    except Exception as le:
        logger.error(f"[LOG DUMP ERROR] {le}")

    # 古いログのクリーンアップ処理
    try:
        retention_days = int(os.environ.get("AIDER_PROXY_LOG_RETENTION_DAYS", 30))
        if retention_days > 0:
            current_time = time.time()
            retention_seconds = retention_days * 24 * 60 * 60
            for filepath in glob.glob(os.path.join(log_dir, "*.json")):
                if os.path.isfile(filepath):
                    file_mtime = os.path.getmtime(filepath)
                    if (current_time - file_mtime) > retention_seconds:
                        os.remove(filepath)
                        logger.debug(f"[LOG CLEANUP] Removed old log: {filepath}")
    except Exception as e:
        logger.error(f"[LOG CLEANUP ERROR] {e}")

def passthrough_proxy(request: Any, url: str) -> Any:
    """
    チャット以外のリクエスト（/v1/modelsなど）をバックエンドへ透過転送する

    Args:
        request: Flaskのrequestオブジェクト
        url: 転送先URL

    Returns:
        FlaskのResponseオブジェクト
    """
    import requests
    from flask import Response
    req_kwargs = {
        'method': request.method,
        'url': url,
        'headers': {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']},
    }
    if request.is_json:
        req_kwargs['json'] = request.get_json(silent=True)
    elif request.data:
        req_kwargs['data'] = request.data

    try:
        resp = requests.request(**req_kwargs)
        return Response(resp.content, status=resp.status_code, headers=dict(resp.headers))
    except Exception as e:
        logger.error(f"[PROXY ERROR] {e}")
        return Response(json.dumps({"error": str(e)}), status=500, mimetype='application/json')



    test_result_marker = "I ran this command:\n\n./.aider/test.sh\n\nAnd got this output:"
    
    # 最新のテスト結果のインデックスを見つける
    latest_test_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get('content', '')
        if isinstance(content, str) and test_result_marker in content:
            latest_test_idx = i
            break
            
    if latest_test_idx == -1:
        return messages
        
    compressed_chars = 0
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        if isinstance(content, str) and i < latest_test_idx and test_result_marker in content:
            parts = content.split("And got this output:\n\n")
            if len(parts) > 1:
                original_len = len(content)
                new_content = parts[0] + "And got this output:\n\n[... Previous test output truncated by StateForge Proxy to save context ...]"
                msg['content'] = new_content
                compressed_chars += (original_len - len(new_content))
                
    if compressed_chars > 0:
        import logging
        logger = logging.getLogger("aider_proxy")
        logger.info(f"[FOCUS MODE] Truncated old test outputs. Saved {compressed_chars} characters.")
        
    return messages




    test_result_marker = "I ran this command:" + chr(10) + chr(10) + "./.aider/test.sh" + chr(10) + chr(10) + "And got this output:"
    repomap_marker = "Here is a summary of the repo:"
    
    # 最新のテスト結果のインデックスを見つける
    latest_test_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get('content', '')
        if isinstance(content, str) and test_result_marker in content:
            latest_test_idx = i
            break
            
    compressed_chars = 0
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        if not isinstance(content, str):
            continue
            
        # 1. RepoMapの圧縮
        if repomap_marker in content:
            original_len = len(content)
            # RepoMapマーカーだけ残して後続を削除
            msg['content'] = repomap_marker + chr(10) + chr(10) + "[... RepoMap removed by StateForge Proxy to prevent context overflow and hallucination ...]"
            compressed_chars += (original_len - len(msg['content']))
            continue # RepoMapメッセージはテスト出力を含まないので次へ
            
        # 2. 古いテスト結果の圧縮
        if i < latest_test_idx and test_result_marker in content:
            parts = content.split("And got this output:" + chr(10) + chr(10))
            if len(parts) > 1:
                original_len = len(content)
                new_content = parts[0] + "And got this output:" + chr(10) + chr(10) + "[... Previous test output truncated by StateForge Proxy to save context ...]"
                msg['content'] = new_content
                compressed_chars += (original_len - len(new_content))
                
    if compressed_chars > 0:
        import logging
        logger = logging.getLogger("aider_proxy")
        logger.info(f"[FOCUS MODE] Truncated repomap/old test outputs. Saved {compressed_chars} characters.")
        
    return messages



def compress_messages(messages: list) -> list:
    """
    過去のテスト実行結果や巨大なRepoMapを切り捨てることでコンテキストを圧縮する。
    環境変数 AIDER_COMPRESS_TESTS と AIDER_COMPRESS_REPOMAP でオンオフを制御可能。
    """
    import os
    
    if not isinstance(messages, list):
        return messages

    # 環境変数の読み込み (デフォルトはオン=1)
    compress_tests = str(os.environ.get('AIDER_COMPRESS_TESTS', '1')).lower() in ['1', 'true', 'yes', 'on']
    compress_repomap = str(os.environ.get('AIDER_COMPRESS_REPOMAP', '1')).lower() in ['1', 'true', 'yes', 'on']

    # どちらもオフなら何もしない
    if not compress_tests and not compress_repomap:
        return messages

    test_result_marker = "I ran this command:\n\n./.aider/test.sh\n\nAnd got this output:"
    repomap_marker = "Here is a summary of the repo:"
    
    # 最新のテスト結果のインデックスを見つける (テスト圧縮がオンの場合のみ)
    latest_test_idx = -1
    if compress_tests:
        for i in range(len(messages) - 1, -1, -1):
            content = messages[i].get('content', '')
            if isinstance(content, str) and test_result_marker in content:
                latest_test_idx = i
                break
            
    compressed_chars = 0
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        if not isinstance(content, str):
            continue
            
        # 1. RepoMapの圧縮
        if compress_repomap and repomap_marker in content:
            original_len = len(content)
            # RepoMapマーカーだけ残して後続を削除
            msg['content'] = repomap_marker + "\n\n[... RepoMap removed by StateForge Proxy to prevent context overflow and hallucination ...]"
            compressed_chars += (original_len - len(msg['content']))
            continue # RepoMapメッセージはテスト出力を含まないので次へ
            
        # 2. 古いテスト結果の圧縮
        if compress_tests and i < latest_test_idx and test_result_marker in content:
            parts = content.split("And got this output:\n\n")
            if len(parts) > 1:
                original_len = len(content)
                new_content = parts[0] + "And got this output:\n\n[... Previous test output truncated by StateForge Proxy to save context ...]"
                msg['content'] = new_content
                compressed_chars += (original_len - len(new_content))
                
    if compressed_chars > 0:
        import logging
        logger = logging.getLogger("aider_proxy")
        logger.info(f"[FOCUS MODE] Truncated repomap/old test outputs. Saved {compressed_chars} characters.")
        
    return messages



def _is_target_file_missing_from_context(original_response: str, messages: list) -> bool:
    """
    LLMが編集しようとしたファイル名が、messages(コンテキスト)に含まれているか確認する。
    含まれていない場合は True (欠落している) を返す。
    """
    if not original_response:
        return False

    # 1. messages から送られたファイル(ソースコード)のリストを抽出する
    shared_files = set()
    pattern = re.compile(r'(?:^|\n)([a-zA-Z0-9_\-\.\/\\ ]+)\n```[a-zA-Z0-9_\-\+]*\n', re.DOTALL)
    for msg in messages:
        if msg.get('role') == 'user':
            content = msg.get('content', '') or ''
            for path in pattern.findall(content):
                path = path.strip()
                if '.' in path or '/' in path or '\\' in path or '\\' in path:
                    shared_files.add(path)

    # 2. LLMレスポンスから「編集しようとしたファイル」を抽出する
    # SEARCHブロックを探す
    search_blocks = re.findall(r'<<<<<<< SEARCH', original_response)
    if not search_blocks:
        return False

    mentioned_files = []
    lines = original_response.split('\n')
    for idx, line in enumerate(lines):
        if '<<<<<<< SEARCH' in line:
            lookback = lines[max(0, idx-4):idx]
            for lbl in reversed(lookback):
                lbl_clean = lbl.strip()
                if re.match(r'^[\w/._-]+\.\w+$', lbl_clean):
                    mentioned_files.append(lbl_clean)
                    break
                m = re.search(r'`([\w/._-]+\.\w+)`', lbl_clean)
                if m:
                    mentioned_files.append(m.group(1))
                    break

    if not mentioned_files:
        return False

    # 3. 編集対象ファイルが、共有ファイルリストに含まれているか検証
    for tf in mentioned_files:
        has_match = False
        for sf in shared_files:
            if sf == tf or sf.endswith('/' + tf) or sf.endswith('\\' + tf):
                has_match = True
                break
        if not has_match:
            return True

    return False

