"""
GitHub API 封装：读写仓库文件、获取图片二进制/Base64。
使用 Personal Access Token 认证。
"""
import base64
import requests
from typing import Optional, List, Dict

class GitHubAPI:
    def __init__(self, token: str, owner: str, repo: str):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def get_file_content(self, path: str) -> Optional[Dict]:
        url = f"{self.base_url}/{path}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content = base64.b64decode(data['content']).decode('utf-8')
        return {'content': content, 'sha': data['sha']}

    def update_file(self, path: str, content: str, sha: str = '', commit_msg: str = 'Update data') -> bool:
        url = f"{self.base_url}/{path}"
        payload = {
            "message": commit_msg,
            "content": base64.b64encode(content.encode('utf-8')).decode('utf-8')
        }
        if sha:
            payload['sha'] = sha
        resp = requests.put(url, headers=self.headers, json=payload)
        return resp.status_code in (200, 201)

    def list_dir(self, path: str) -> List[Dict]:
        url = f"{self.base_url}/{path}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            return []
        return resp.json()

    def get_image_bytes(self, path: str) -> Optional[bytes]:
        raw_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/main/{path}"
        resp = requests.get(raw_url)
        if resp.status_code != 200:
            return None
        return resp.content

    def get_image_base64(self, path: str) -> Optional[str]:
        data = self.get_image_bytes(path)
        if data is None:
            return None
        return base64.b64encode(data).decode('utf-8')