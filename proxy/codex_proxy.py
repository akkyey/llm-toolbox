# codex_proxy.py
import json
import time
import logging
import os
from flask import Flask, request, Response
import requests

import proxy_common
from proxy_etags import EtagsResolver
from proxy_metrics import ProxyMetricsCollector

logger = logging.getLogger("codex_proxy")
app = Flask(__name__)

LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:9090")
PORT = int(os.environ.get("CODEX_PROXY_PORT", 9094))

codex_metrics = ProxyMetricsCollector("codex_proxy", PORT)
etags_resolver = EtagsResolver(tags_file=os.path.join(os.getcwd(), "tags"))

@app.route('/v1/responses', methods=['POST'])
def handle_responses():
    """OpenAI Responses API スキーマを通常の Chat Completions 形式へ相互変換・架け橋する"""
    codex_data = request.get_json(silent=True) or {}
    raw_inputs = codex_data.get("input", [])
    
    messages = []
    for item in raw_inputs:
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            content = " ".join([c.get("text", "") for c in content if c.get("type") == "text"])
        messages.append({"role": role, "content": content})
    
    # Etags & メッセージ圧縮
    usr_msg = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
    if usr_msg and etags_resolver.tags_cache:
        matched = etags_resolver.find_symbols_in_text(usr_msg['content'])
        if matched:
            etags_context = "\n\n<workspace_symbols_hint>\n"
            for sym in matched:
                etags_context += f"- Symbol `{sym['symbol']}` in `{sym['file']}`: `{sym['signature']}`\n"
            etags_context += "</workspace_symbols_hint>"
            usr_msg['content'] += etags_context

    messages = proxy_common.compress_messages(messages)
    
    chat_request = {
        "model": codex_data.get("model", "qwen"),
        "messages": messages,
        "temperature": codex_data.get("temperature", 0.1),
        "stream": False
    }
    
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}
    try:
        resp = requests.post(f"{LLAMA_SERVER_URL}/v1/chat/completions", json=chat_request, headers=headers, timeout=120)
        resp.raise_for_status()
        chat_res = resp.json()
        
        choices = chat_res.get("choices", [{}])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        
        codex_response = {
            "id": chat_res.get("id", "resp_codex"),
            "object": "response",
            "created": int(time.time()),
            "model": chat_res.get("model", "local-model"),
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": content}]
            }],
            "usage": chat_res.get("usage", {})
        }
        codex_metrics.record_request_complete()
        return Response(json.dumps(codex_response, ensure_ascii=False), status=200, mimetype='application/json')
    except Exception as e:
        logger.error(f"[CODEX API BRIDGE ERROR] {e}")
        return Response(json.dumps({"error": {"message": str(e)}}), status=500, mimetype='application/json')

@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def passthrough(path):
    return proxy_common.passthrough_proxy(request, f"{LLAMA_SERVER_URL}/{path}")

if __name__ == '__main__':
    app.run(port=PORT, host='127.0.0.1')
