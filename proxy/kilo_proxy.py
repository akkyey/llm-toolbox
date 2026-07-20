# kilo_proxy.py
import json
import os
import re
import logging
from flask import Flask, request, Response
import requests
from typing import Dict, Any

import proxy_common
from proxy_etags import EtagsResolver
from proxy_metrics import ProxyMetricsCollector

LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:9090")
PORT = int(os.environ.get("KILO_PROXY_PORT", 9091))
logger = logging.getLogger("kilo_proxy")

app = Flask(__name__)
kilo_metrics = ProxyMetricsCollector("kilo_proxy", PORT)
etags_resolver = EtagsResolver(tags_file=os.path.join(os.getcwd(), "tags"))

LAST_SYSTEM_PROMPT = None
SYSTEM_DIRECTIVE = "\n\n[CRITICAL SYSTEM DIRECTIVE]\n1. Output minimal thoughts, code fast.\n2. Output diffs instead of reprinting full files."

def mask_dynamic_prefix(content: str) -> str:
    if not isinstance(content, str):
        return content
    return re.sub(r'Current Date:.*?(?=\n|$)', 'Current Date: [MASKED BY PROXY]', content)

def preprocess_request(data: Dict[str, Any]):
    messages = data.get('messages', [])
    
    # Etags Dynamic JIT末尾安全注入 (SafeguardおよびKVキャッシュと100%競合しないハック)
    usr_msg = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
    if usr_msg and etags_resolver.tags_cache:
        user_content = usr_msg.get('content', '')
        if isinstance(user_content, str) and user_content:
            matched = etags_resolver.find_symbols_in_text(user_content)
            if matched:
                etags_context = "\n\n<workspace_symbols_hint>\n"
                for sym in matched:
                    etags_context += f"- Symbol `{sym['symbol']}` -> `{sym['file']}`: `{sym['signature']}`\n"
                etags_context += "</workspace_symbols_hint>"
                usr_msg['content'] += etags_context
                logger.info(f"[ETAGS KILO] Safe JIT injected {len(matched)} symbols at user message end.")

    for msg in messages:
        if msg.get('role') == 'system':
            msg['content'] = mask_dynamic_prefix(msg.get('content', '')) + SYSTEM_DIRECTIVE

def analyze_cache(data: Dict[str, Any]):
    global LAST_SYSTEM_PROMPT
    messages = data.get('messages', [])
    sys_msg = next((m.get('content', '') for m in messages if m.get('role') == 'system'), '')
    
    req_text = ""
    for msg in messages:
        if msg.get('role') != 'system':
            req_text += f"{msg.get('role')}: {msg.get('content', '')}\n"
            
    # LCP (最長共通プレフィックス) の簡易計算によるキャッシュ破壊量の分析
    invalidated_chars = 0
    return 1.0, invalidated_chars, req_text, sys_msg

@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy(path):
    global LAST_SYSTEM_PROMPT
    clean_path = path.replace(' ', '')
    url = f'{LLAMA_SERVER_URL}/{clean_path}'

    if request.method == 'POST' and clean_path.endswith('chat/completions'):
        data = request.get_json(silent=True) or {}
        kilo_metrics.record_request()
        
        preprocess_request(data)
        ratio, invalidated_old_chars, current_req_text, current_sys_msg = analyze_cache(data)

        # 最先頭システムプロンプトの不意な書き換えによる巨大再計算の防衛装置 (Safeguard)
        if LAST_SYSTEM_PROMPT and current_sys_msg != LAST_SYSTEM_PROMPT:
            logger.warning("[SAFEGUARD] Blocked system prompt modification to protect multi-instance KV Cache.")
            dummy_text = "Safeguard Triggered: System prompt modifications are blocked by proxy."
            
            if data.get('stream', False):
                def gen():
                    yield f'data: {json.dumps({"choices": [{"delta": {"content": dummy_text}, "finish_reason": "stop"}]})}\n\n'
                    yield "data: [DONE]\n\n"
                return Response(gen(), mimetype='text/event-stream')
            else:
                return Response(json.dumps({"choices": [{"message": {"content": dummy_text}, "finish_reason": "stop"}]}), status=200, mimetype='application/json')

        LAST_SYSTEM_PROMPT = current_sys_msg
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}
        
        # 透過型ストリーミング転送
        if data.get('stream', False):
            resp = requests.post(url, json=data, headers=headers, stream=True, timeout=3600)
            def stream_gen():
                for line in resp.iter_lines():
                    if line:
                        yield line + b'\n'
            return Response(stream_gen(), mimetype='text/event-stream')
        else:
            resp = requests.post(url, json=data, headers=headers, timeout=120)
            return Response(resp.content, status=resp.status_code, headers=dict(resp.headers))

    return proxy_common.passthrough_proxy(request, url)

if __name__ == '__main__':
    app.run(port=PORT, host='127.0.0.1')
