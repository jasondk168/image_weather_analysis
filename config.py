"""
配置管理：读取 GitHub Token、仓库信息、模型名。
支持本地 .env 和云端 streamlit secrets 两种来源。
所有路径基于 __file__ 相对计算，确保便携性。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

def load_config() -> dict:
    config = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)
    try:
        import streamlit as st
        sec = st.secrets.to_dict()
        config['token'] = sec.get('GITHUB_TOKEN', os.getenv('GITHUB_TOKEN', ''))
        config['owner'] = sec.get('GITHUB_REPO_OWNER', os.getenv('GITHUB_REPO_OWNER', ''))
        config['repo'] = sec.get('GITHUB_REPO_NAME', os.getenv('GITHUB_REPO_NAME', ''))
        config['model'] = sec.get('AI_MODEL', os.getenv('AI_MODEL', 'gpt-4o'))
    except Exception:
        config['token'] = os.getenv('GITHUB_TOKEN', '')
        config['owner'] = os.getenv('GITHUB_REPO_OWNER', '')
        config['repo'] = os.getenv('GITHUB_REPO_NAME', '')
        config['model'] = os.getenv('AI_MODEL', 'gpt-4o')
    config['images_local_dir'] = PROJECT_ROOT / 'input'
    config['images_remote_dir'] = 'images/'
    config['data_file_local'] = PROJECT_ROOT / 'data' / 'analysis_records.json'
    config['data_file_remote'] = 'data/analysis_records.json'
    config['data_dir'] = PROJECT_ROOT / 'data'
    return config