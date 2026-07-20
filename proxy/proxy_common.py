#!/usr/bin/env python3
"""
AI Agent Proxy 共通ミドルウェア・エンジン (SyntaxErrorクリーンアップ＆汎用版)
"""
import glob
import time
import json
import logging
import re
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def send_dummy_chunks(content: str, res: Dict[str, Any], finish_reason: Optional[str] = "stop", tool_calls: Optional[List[Dict[str, Any]]] = None) -> Any:
    """
    エージェントのパーサーが正常に動作するよう、擬似ストリーミング（小分け送信）を行う
    """
    chunk_size = 40
    delay = 0.01

    for i in range(0, len(content), chunk_size):
        chunk_text = content[i:i+chunk_size]
        chunk_data = {
            "id": res.get("id", "chatcmpl-proxy-resp"),
            "object": "chat.completion.chunk",
            "created": res.get("created", int(time.time())),
            "model": res.get("model", "local-model"),
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

    last_chunk = {
        "id": res.get("id", "chatcmpl-proxy-resp"),
        "object": "chat.completion.chunk",
        "created": res.get("created", int(time.time())),
        "model": res.get("model", "local-model"),
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason
        }]
    }
    if tool_calls:
        last_chunk["choices"][0]["delta"]["tool_calls"] = tool_calls

    yield f"data: {json.dumps(last_chunk, ensure_ascii=False)}\n\n"

def detect_expected_fence(data: Dict[str, Any]) -> str:
    """Aiderの期待するフェンス（バッククォートの数）を検出"""
    expected_fence = "```"
    for msg in data.get('messages', []):
        if msg.get('role') == 'system':
            sys_content = msg.get('content', '')
            match = re.search(r'path/to/filename\.[a-zA-Z0-9]+\s+(`{3,5})', sys_content)
            if match:
                expected_fence = match.group(1)
                logger.info(f"[FENCE DETECT] Aider expects fence: {expected_fence}")
                break
    return expected_fence

def standardize_fences(content: str, expected_fence: str) -> str:
    """レスポンス内のフェンスをAiderの期待値に統一"""
    replaced_content = re.sub(r'^\s*`{3,5}[a-zA-Z0-9_-]*\s*$', expected_fence, content, flags=re.MULTILINE)
    if replaced_content != content:
        logger.info(f"[FILTER] Standardized fences to match expectations ({expected_fence})")
        content = replaced_content
    return content

def compress_messages(messages: list) -> list:
    """
    過去のテスト実行結果や巨大なRepoMapを切り捨てることで文脈を効率的に圧縮する。
    """
    if not isinstance(messages, list):
        return messages

    compress_tests = str(os.environ.get('AIDER_COMPRESS_TESTS', '1')).lower() in ['1', 'true', 'yes', 'on']
    compress_repomap = str(os.environ.get('AIDER_COMPRESS_REPOMAP', '1')).lower() in ['1', 'true', 'yes', 'on']

    if not compress_tests and not compress_repomap:
        return messages

    test_result_marker = "I ran this command:\n\n./.aider/test.sh\n\nAnd got this output:"
    repomap_marker = "Here is a summary of the repo:"
    
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
            
        if compress_repomap and repomap_marker in content:
            original_len = len(content)
            msg['content'] = repomap_marker + "\n\n[... RepoMap removed by Proxy to prevent context overflow and hallucination ...]"
            compressed_chars += (original_len - len(msg['content']))
            continue
            
        if compress_tests and i < latest_test_idx and test_result_marker in content:
            parts = content.split("And got this output:\n\n")
            if len(parts) > 1:
                original_len = len(content)
                new_content = parts[0] + "And got this output:\n\n[... Previous test output truncated by Proxy to save context ...]"
                msg['content'] = new_content
                compressed_chars += (original_len - len(new_content))
                
    if compressed_chars > 0:
        logger.info(f"[FOCUS MODE] Truncated repomap/old test outputs. Saved {compressed_chars} characters.")
        
    return messages

def save_aider_log(data: Dict[str, Any], original_response: str, filtered_response: str, log_dir: Optional[str] = None) -> None:
    """Aiderの実行ログを構造化保存して分析を容易にする"""
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

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "failure_reason": failure_reason,
                "request": data,
                "original_response": original_response or "",
                "filtered_response": filtered_response
            }, f, ensure_ascii=False, indent=2)
    except Exception as le:
        logger.error(f"[LOG DUMP ERROR] {le}")

    # ログファイルの保存期間クリーンアップ(デフォルト30日)
    try:
        retention_days = int(os.environ.get("AIDER_PROXY_LOG_RETENTION_DAYS", 30))
        if retention_days > 0:
            current_time = time.time()
            retention_seconds = retention_days * 24 * 60 * 60
            for filepath in glob.glob(os.path.join(log_dir, "*.json")):
                if os.path.isfile(filepath):
                    if (current_time - os.path.getmtime(filepath)) > retention_seconds:
                        os.remove(filepath)
    except Exception as e:
        logger.error(f"[LOG CLEANUP ERROR] {e}")

def passthrough_proxy(request: Any, url: str) -> Any:
    """チャット以外の付随エンドポイント（/v1/modelsなど）をバックエンドへ透過転送する"""
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
        logger.error(f"[PASSTHROUGH PROXY ERROR] {e}")
        return Response(json.dumps({"error": str(e)}), status=500, mimetype='application/json')
