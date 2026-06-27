"""
数据持久化：JSON 格式，本地直接读写文件，云端通过 GitHub API 同步。
"""
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class DataStore:
    def __init__(self, config: dict, github_api: Optional['GitHubAPI'] = None):
        self.local_path = config['data_file_local']
        self.remote_path = config['data_file_remote']
        self.github_api = github_api
        self._ensure_dir()

    def _ensure_dir(self):
        self.local_path.parent.mkdir(parents=True, exist_ok=True)

    def load_records(self) -> List[Dict]:
        # 优先从远程加载（如果有 github_api）
        if self.github_api and self.github_api.token:
            try:
                remote = self.github_api.get_file_content(self.remote_path)
                if remote:
                    content = remote['content']
                    # 检查内容是否为空或空白
                    if content and content.strip():
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            # 如果 JSON 解析失败，回退到本地
                            pass
            except Exception:
                # 网络或其他异常，静默回退到本地
                pass

        # 远程失败/不可用，从本地文件加载
        if self.local_path.exists():
            try:
                with open(self.local_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content and content.strip():
                        return json.loads(content)
            except (json.JSONDecodeError, IOError):
                pass

        return []

    def save_records(self, records: List[Dict], commit_msg: str = "Update analysis records"):
        # 始终保存到本地
        self._ensure_dir()
        with open(self.local_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        # 尝试同步到 GitHub
        if self.github_api and self.github_api.token:
            try:
                remote = self.github_api.get_file_content(self.remote_path)
                sha = remote['sha'] if remote else ''
                new_content = json.dumps(records, ensure_ascii=False, indent=2)
                self.github_api.update_file(self.remote_path, new_content, sha, commit_msg)
            except Exception:
                # 同步失败不影响本地
                pass

    def add_record(self, new_record: Dict):
        records = self.load_records()
        records.append(new_record)
        self.save_records(records, f"Add record {new_record.get('id', datetime.now().isoformat())}")

    def export_to_json(self, filepath: Path):
        records = self.load_records()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        return filepath
