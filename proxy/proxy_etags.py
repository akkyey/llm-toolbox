# proxy_etags.py
import os
import re
import logging
from typing import Dict, List, Any

logger = logging.getLogger("proxy_common.etags")

class EtagsResolver:
    def __init__(self, tags_file: str = "tags"):
        self.tags_file = tags_file
        self.tags_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.load_tags()

    def load_tags(self):
        """ctags形式のファイルをロードしてキャッシュ化する"""
        if not os.path.exists(self.tags_file):
            logger.warning(f"[ETAGS] tags file not found at: {self.tags_file}. Please run 'ctags -R' in your workspace.")
            return
        
        try:
            count = 0
            with open(self.tags_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.startswith('!'):  # メタデータのスキップ
                        continue
                    parts = line.strip().split('	')
                    if len(parts) >= 3:
                        symbol = parts[0]
                        file_path = parts[1]
                        pattern = parts[2]
                        
                        # regexパターンから定義シグネチャを簡易抽出
                        signature = pattern
                        if pattern.startswith('/^') and pattern.endswith('$/;"'):
                            signature = pattern[2:-4].strip()
                        elif pattern.startswith('/') and pattern.endswith('/;"'):
                            signature = pattern[1:-3].strip()
                        
                        if symbol not in self.tags_cache:
                            self.tags_cache[symbol] = []
                        
                        self.tags_cache[symbol].append({
                            "file": file_path,
                            "signature": signature
                        })
                        count += 1
            logger.info(f"[ETAGS] Successfully loaded {count} symbols from {self.tags_file}")
        except Exception as e:
            logger.error(f"[ETAGS] Failed to load tags: {e}")

    def find_symbols_in_text(self, text: str) -> List[Dict[str, Any]]:
        """入力テキストからシンボル名（クラス、関数名など）を抽出し、tagsから定義情報を検索する"""
        if not self.tags_cache:
            return []
            
        # キャメルケース、スネークケース、英数字とアンダースコアにマッチする単語を抽出
        words = set(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text))
        
        # 検索ノイズになりがちな一般的な予約語や短い単語を無視
        ignore_words = {"def", "class", "import", "from", "return", "self", "None", "true", "false", "str", "int", "dict", "list"}
        filtered_words = [w for w in words if len(w) > 3 and w not in ignore_words]
        
        results = []
        for word in filtered_words:
            if word in self.tags_cache:
                for entry in self.tags_cache[word]:
                    # 重複登録を避ける
                    if not any(r['symbol'] == word and r['file'] == entry['file'] for r in results):
                        results.append({
                            "symbol": word,
                            "file": entry["file"],
                            "signature": entry["signature"]
                        })
        return results
