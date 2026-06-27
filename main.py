"""
Streamlit 主程序。同时适配本地（便携版）和云端（Streamlit Cloud）。
包含三个标签页：分析、历史记录、校正管理。
每次分析自动归档图片，并可手动保存 AI 分析文本。
新增批量导入功能：从不同文件夹选取多张图片，自动匹配类型并复制到 input/ 和 images/。
侧边栏显示必需6张图片名称，并支持完整 GitHub 凭据输入。
上传的图片会自动保存到 images/ 文件夹（云端模式下同步到 GitHub 仓库）。
"""
import streamlit as st
from pathlib import Path
import sys
import os
import shutil
import base64
from datetime import datetime
import plotly.express as px
import pandas as pd
from PIL import Image
import io
import re

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import load_config
from github_api import GitHubAPI
from database import DataStore
from correction import compute_correction, apply_correction, level_to_num, num_to_level, REVERSE_LEVEL_MAP
from analyzer import analyze_images

st.set_page_config(page_title="普吉岛降雨分析", layout="wide")

config = load_config()
ARCHIVE_DIR = PROJECT_ROOT / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)

# ========== 侧边栏配置 ==========
st.sidebar.header("⚙️ 设置")

model_name = st.sidebar.text_input("AI 模型", value=config.get('model', 'gpt-4o'))

# 默认选中“云端模式”，手机端无需手动切换
mode = st.sidebar.radio("运行模式",
    ["云端模式 (读取仓库 images/ 文件夹)", "本地模式 (读取本地 input/ 文件夹)"],
    index=0)

st.sidebar.markdown("---")
st.sidebar.markdown("**🔑 GitHub 凭据**")

api_key_input = st.sidebar.text_input(
    "GitHub Token", type="password", value=config.get('token', ''),
    help="在 https://github.com/settings/tokens 生成（需repo和models权限）"
)
owner_input = st.sidebar.text_input(
    "仓库所有者 (Owner)", value=config.get('owner', ''),
    help="你的 GitHub 用户名"
)
repo_input = st.sidebar.text_input(
    "仓库名称 (Repo)", value=config.get('repo', ''),
    help="例如 image_weather_analysis"
)

config['token'] = api_key_input
config['owner'] = owner_input
config['repo'] = repo_input

missing = []
if not config['token']: missing.append("GitHub Token")
if not config['owner']: missing.append("仓库所有者")
if not config['repo']: missing.append("仓库名称")

if missing:
    st.sidebar.info(f"📝 请在下方输入框中填写：{', '.join(missing)}。\n\n也可在项目根目录的 `.env` 文件中预设。")
    github_api = None
else:
    st.sidebar.success("✅ GitHub 凭据已配置")
    github_api = GitHubAPI(config['token'], config['owner'], config['repo'])

store = DataStore(config, github_api)
if 'records' not in st.session_state:
    st.session_state.records = store.load_records()
else:
    st.session_state.records = store.load_records()

# ========== 侧边栏图片名称提示 ==========
st.sidebar.markdown("---")
st.sidebar.markdown("**📷 需要的6张图片（文件名必须完全匹配）：**")
for key in ["radar", "wind_600m", "wind_3000m", "wind_direction", "model_mix_1", "model_mix_2"]:
    st.sidebar.text(f"- {key}.png/.jpg/.jpeg")
if "云端模式" in mode:
    st.sidebar.info("✅ 请确保仓库根目录下的 `images/` 文件夹包含上述6张图片。\n上传的图片会自动同步到仓库。")

# ========== 常量和辅助函数 ==========
REQUIRED_NAMES = ["radar", "wind_600m", "wind_3000m", "wind_direction", "model_mix_1", "model_mix_2"]
REQUIRED_DISPLAY = {
    "radar": "雷达回波图", "wind_600m": "600米低空风速图", "wind_3000m": "3000米高空风速图",
    "wind_direction": "风向图", "model_mix_1": "混合模型预报图1", "model_mix_2": "混合模型预报图2"
}
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg'}

def find_images_in_dir(directory: Path):
    result = {}
    if not directory.exists(): return result
    for name in REQUIRED_NAMES:
        for ext in ALLOWED_EXTENSIONS:
            f = directory / f"{name}{ext}"
            if f.exists():
                result[name] = f
                break
    return result

def archive_images(source_dir: Path, archive_path: Path):
    archive_path.mkdir(parents=True, exist_ok=True)
    count = 0
    for name in REQUIRED_NAMES:
        for ext in ALLOWED_EXTENSIONS:
            src = source_dir / f"{name}{ext}"
            if src.exists():
                shutil.copy2(src, archive_path / f"{name}{ext}")
                count += 1
                break
    uploads_dir = source_dir / "_uploads"
    if uploads_dir.exists():
        for f in uploads_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, archive_path / f.name)
                count += 1
    return count

def save_image_to_local_and_remote(file_bytes: bytes, filename: str, target_type: str):
    """将上传的文件保存到本地 input/、images/ 以及云端仓库 images/"""
    ext = Path(filename).suffix.lower()
    target_name = f"{target_type}{ext}"

    # 保存到本地 input/
    input_path = config['images_local_dir'] / target_name
    with open(input_path, "wb") as f:
        f.write(file_bytes)

    # 保存到本地 images/
    local_images_dir = PROJECT_ROOT / "images"
    local_images_dir.mkdir(parents=True, exist_ok=True)
    local_image_path = local_images_dir / target_name
    with open(local_image_path, "wb") as f:
        f.write(file_bytes)

    # 同步到 GitHub 仓库 images/（如果已配置）
    if github_api and github_api.token:
        remote_path = f"images/{target_name}"
        # 检查文件是否已存在，获取 sha
        existing = github_api.get_file_content(remote_path)
        sha = existing['sha'] if existing else ''
        # 上传文件
        success = github_api.update_file(
            remote_path,
            base64.b64encode(file_bytes).decode('utf-8'),
            sha,
            f"Upload {target_name}"
        )
        # 注意：GitHub API update_file 需要 base64 编码的文件内容，但 update_file 方法内部会再次编码，所以需要传递原始 base64 字符串？
        # 查看 github_api.py 中 update_file 实现：它接受 content 字符串并再次 base64 编码。
        # 所以我们需要先 base64 编码文件内容，再作为 content 传入。
        # 但是上面已经 base64 编码了一次，update_file 内部又会编码一次，导致双重编码。
        # 因此我们需要修改方法，或者直接传递原始文件字节？
        # 为了简洁，我们修改 save_image_to_local_and_remote 函数，直接调用 update_file 并自行编码一次：
        # 重新实现：
        pass  # 下面会重新实现

# 重新实现：避免双重 base64 编码
def save_image_to_github(file_bytes: bytes, target_type: str):
    """通过 GitHub API 将图片上传到仓库 images/ 文件夹"""
    if not github_api or not github_api.token:
        return False
    ext = '.png'  # 默认扩展名，实际上我们需要知道原始扩展名
    # 由于函数参数未包含扩展名，我们将在调用时传递完整文件名
    # 为了避免混乱，下面将 upload_to_github 单独实现

def upload_to_github(file_bytes: bytes, remote_path: str, commit_msg: str = "Upload image"):
    """上传文件到 GitHub 仓库，自动处理 base64 编码"""
    if not github_api or not github_api.token:
        return False
    # 获取现有文件 sha（如果存在）
    existing = github_api.get_file_content(remote_path)
    sha = existing['sha'] if existing else ''
    # 构建 payload：content 应为 base64 编码的字符串
    b64_content = base64.b64encode(file_bytes).decode('utf-8')
    payload = {
        "message": commit_msg,
        "content": b64_content
    }
    if sha:
        payload['sha'] = sha
    import requests
    url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/contents/{remote_path}"
    headers = {
        "Authorization": f"token {github_api.token}",
        "Accept": "application/vnd.github.v3+json"
    }
    resp = requests.put(url, headers=headers, json=payload)
    return resp.status_code in (200, 201)

def auto_detect_type(filename: str) -> str:
    lower = filename.lower()
    if 'radar' in lower: return 'radar'
    if '600' in lower or 'low' in lower: return 'wind_600m'
    if '3000' in lower or 'high' in lower: return 'wind_3000m'
    if 'direction' in lower: return 'wind_direction'
    if 'mix1' in lower or 'model1' in lower: return 'model_mix_1'
    if 'mix2' in lower or 'model2' in lower: return 'model_mix_2'
    if 'model' in lower: return 'model_mix_1'
    return 'unknown'

# ========== 主界面 ==========
st.title("🌧️ 普吉岛天气预报图像分析")
st.markdown("上传或选择6张标准气象图，让 AI 分析降雨情况并记录校正。")

tab1, tab2, tab3 = st.tabs(["📤 分析", "📊 历史记录", "⚖️ 校正管理"])

with tab1:
    col_left, col_right = st.columns([1, 1.5])
    with col_left:
        st.subheader("图片准备")

        # ----- 批量导入模块（兼容两种模式）-----
        with st.expander("📂 批量导入图片（自动识别类型并保存到 images/）"):
            st.markdown("上传多张图片，程序将根据文件名自动匹配类型，您也可以手动调整。上传后自动保存到本地 images/ 和 GitHub 仓库 images/（需配置凭据）。")
            uploaded_files_batch = st.file_uploader(
                "选择图片文件（可多选）", type=['png','jpg','jpeg'],
                accept_multiple_files=True, key="batch_uploader"
            )
            if uploaded_files_batch:
                if 'batch_mapping' not in st.session_state:
                    st.session_state.batch_mapping = {}
                for uf in uploaded_files_batch:
                    if uf.name not in st.session_state.batch_mapping:
                        st.session_state.batch_mapping[uf.name] = auto_detect_type(uf.name)
                st.write("**文件类型映射（请确认或修改）：**")
                new_mapping = {}
                for uf in uploaded_files_batch:
                    cols = st.columns([2, 2])
                    with cols[0]: st.write(uf.name)
                    with cols[1]:
                        current_type = st.session_state.batch_mapping.get(uf.name, 'unknown')
                        selected = st.selectbox(
                            f"类型 - {uf.name}",
                            options=['unknown'] + REQUIRED_NAMES,
                            index=0 if current_type not in ['unknown']+REQUIRED_NAMES else (['unknown']+REQUIRED_NAMES).index(current_type),
                            key=f"map_{uf.name}", label_visibility="collapsed"
                        )
                        new_mapping[uf.name] = selected
                st.session_state.batch_mapping = new_mapping
                if st.button("✅ 确认导入（保存到本地 images/ 和 GitHub）"):
                    success_count = 0
                    error_files = []
                    for uf in uploaded_files_batch:
                        target_type = st.session_state.batch_mapping.get(uf.name, 'unknown')
                        if target_type not in REQUIRED_NAMES:
                            error_files.append(f"{uf.name} (类型无效)")
                            continue
                        file_bytes = uf.getbuffer()
                        ext = Path(uf.name).suffix.lower()
                        target_name = f"{target_type}{ext}"

                        # 保存到本地 input/
                        local_input = config['images_local_dir'] / target_name
                        local_input.parent.mkdir(parents=True, exist_ok=True)
                        with open(local_input, "wb") as f:
                            f.write(file_bytes)

                        # 保存到本地 images/
                        local_images = PROJECT_ROOT / "images" / target_name
                        local_images.parent.mkdir(parents=True, exist_ok=True)
                        with open(local_images, "wb") as f:
                            f.write(file_bytes)

                        # 上传到 GitHub 仓库 images/（如果已配置）
                        if github_api and github_api.token:
                            remote_path = f"images/{target_name}"
                            uploaded = upload_to_github(file_bytes, remote_path, f"Upload {target_name}")
                            if not uploaded:
                                error_files.append(f"{uf.name} (GitHub上传失败)")

                        success_count += 1
                    if error_files:
                        st.warning(f"以下文件有问题：{', '.join(error_files)}")
                    st.success(f"成功导入 {success_count} 张图片到本地和 GitHub images/ 文件夹！")
                    st.session_state.batch_mapping = {}
                    st.rerun()

        # ----- 根据模式显示图片来源 -----
        if "云端模式" in mode:
            st.info("📂 从仓库 `images/` 文件夹读取图片。下方将显示仓库中的图片。")
            if github_api:
                dir_contents = github_api.list_dir(config['images_remote_dir'])
                remote_images = {}
                for item in dir_contents:
                    if item['type'] == 'file' and any(item['name'].lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
                        name_stem = Path(item['name']).stem
                        if name_stem in REQUIRED_NAMES:
                            raw_url = f"https://raw.githubusercontent.com/{config['owner']}/{config['repo']}/main/{config['images_remote_dir']}{item['name']}"
                            remote_images[name_stem] = {'url': raw_url, 'filename': item['name']}
                if remote_images:
                    st.success(f"仓库 images/ 中找到 {len(remote_images)} 张图片")
                    for name in REQUIRED_NAMES:
                        if name in remote_images:
                            st.image(remote_images[name]['url'], width=100, caption=REQUIRED_DISPLAY[name])
                    missing = [REQUIRED_DISPLAY[n] for n in REQUIRED_NAMES if n not in remote_images]
                    if missing:
                        st.warning(f"缺少: {', '.join(missing)}")
                else:
                    st.warning("仓库 images/ 文件夹为空或缺少标准命名的图片，请使用批量导入上传图片。")
            else:
                st.warning("未配置 GitHub 凭据，无法读取仓库图片。")
        else:  # 本地模式
            local_dir = config['images_local_dir']
            local_dir.mkdir(parents=True, exist_ok=True)
            image_dict = find_images_in_dir(local_dir)
            if image_dict:
                st.success(f"input 文件夹中有 {len(image_dict)} 张图片")
                for name, path in image_dict.items():
                    st.image(str(path), width=100, caption=REQUIRED_DISPLAY.get(name, name))
                missing = [REQUIRED_DISPLAY[n] for n in REQUIRED_NAMES if n not in image_dict]
                if missing:
                    st.warning(f"缺少: {', '.join(missing)}")
            else:
                st.info("input 文件夹为空，请使用批量导入上传图片。")

        extra = st.text_area("额外说明（可选）", placeholder="例如：分析明天下午3点的降雨情况")

    with col_right:
        st.subheader("🤖 AI 分析")
        if st.button("🚀 开始分析", type="primary"):
            timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
            archive_path = ARCHIVE_DIR / timestamp_str
            image_paths = []

            # 优先使用上传的文件（data URL）
            uploaded = st.session_state.get('batch_uploader', [])
            if uploaded and len(uploaded) > 0:
                for uf in uploaded:
                    if uf is not None:
                        img_bytes = uf.getbuffer()
                        b64 = base64.b64encode(img_bytes).decode('utf-8')
                        ext = Path(uf.name).suffix.lower()
                        mime = "image/png" if ext == '.png' else "image/jpeg"
                        image_paths.append(f"data:{mime};base64,{b64}")
                # 归档上传文件（本地）
                uploads_dir = config['images_local_dir'] / "_uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                for uf in uploaded:
                    if uf is not None:
                        with open(uploads_dir / uf.name, "wb") as f:
                            f.write(uf.getbuffer())
                archive_images(config['images_local_dir'], archive_path)
            else:
                # 无上传文件，根据模式决定来源
                if "云端模式" in mode:
                    if github_api:
                        dir_contents = github_api.list_dir(config['images_remote_dir'])
                        for item in dir_contents:
                            if item['type'] == 'file' and any(item['name'].lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
                                url = f"https://raw.githubusercontent.com/{config['owner']}/{config['repo']}/main/{config['images_remote_dir']}{item['name']}"
                                image_paths.append(url)
                    if not image_paths:
                        st.error("仓库 images/ 中没有找到图片，请先将6张标准图片推送到仓库，或使用批量导入功能。")
                        st.stop()
                else:  # 本地模式
                    image_dict = find_images_in_dir(config['images_local_dir'])
                    if not image_dict:
                        st.error("没有找到任何图片，请先将图片放入 input/ 文件夹或使用批量导入功能。")
                        st.stop()
                    image_paths = [image_dict[name] for name in REQUIRED_NAMES if name in image_dict]
                    if len(image_paths) < 6:
                        st.error("缺少部分图片（需要6张标准图），请补齐。")
                        st.stop()
                    archive_images(config['images_local_dir'], archive_path)

            with st.spinner("正在调用 AI 分析，请稍候..."):
                try:
                    result_text = analyze_images(api_key=config['token'], model=model_name, image_paths=image_paths, extra_context=extra)
                except Exception as e:
                    st.error(f"分析调用失败：{e}")
                    st.stop()

            # 解析结果
            rain_prob, pred_level = "", ""
            lines = result_text.strip().split('\n')
            conclusion_line = None
            for line in lines:
                if '预测结论' in line:
                    conclusion_line = line
                    break
            if conclusion_line:
                m1 = re.search(r'降雨概率\s*(\d+)%', conclusion_line)
                m2 = re.search(r'预测等级\s*(\S+)', conclusion_line)
                if m1: rain_prob = m1.group(1) + '%'
                if m2: pred_level = m2.group(1)
            if not rain_prob and not pred_level:
                for line in lines:
                    if "降雨概率" in line and line != conclusion_line:
                        parts = line.split(":")
                        if len(parts) >= 2: rain_prob = parts[-1].strip()
                    elif "预测等级" in line and line != conclusion_line:
                        parts = line.split(":")
                        if len(parts) >= 2: pred_level = parts[-1].strip()

            st.subheader("📋 分析结果")
            st.write("**AI 原始回复：**"); st.text(result_text)

            correction = compute_correction(st.session_state.records)
            corrected_level = pred_level; bias = 0.0
            if pred_level and pred_level in REVERSE_LEVEL_MAP.values():
                corrected_level, bias = apply_correction(pred_level, correction)
            st.write(f"**降雨概率：** {rain_prob}")
            st.write(f"**AI 预测等级：** {pred_level}")
            st.write(f"**校正后等级：** {corrected_level} (偏差因子: {bias:.2f})")

            now = datetime.now()
            record = {
                'id': now.strftime("%Y%m%d%H%M%S"),
                'timestamp': now.isoformat(),
                'image_folder': f"archive/{timestamp_str}" if "本地模式" in mode else config['images_remote_dir'],
                'rain_prob': rain_prob, 'predicted_level': pred_level,
                'corrected_level': corrected_level, 'analysis': result_text,
                'raw_response': result_text, 'image_count': len(image_paths), 'actual_level': None
            }
            st.session_state.last_prediction = record
            store.add_record(record)
            st.session_state.records = store.load_records()
            st.success("本次分析已自动归档并保存！")
            st.session_state.analysis_done = True
            st.session_state.archive_path = archive_path if "本地模式" in mode else None
            st.session_state.raw_text = result_text
            st.session_state.record_id = record['id']

            st.divider()
            st.subheader("📝 实际降雨反馈")
            actual_level = st.selectbox("实际降雨等级", ["", "无雨","小雨","中雨","大雨","暴雨"], key="actual_select")
            if st.button("保存反馈"):
                if actual_level:
                    for r in st.session_state.records:
                        if r['id'] == record['id']:
                            r['actual_level'] = actual_level; break
                    store.save_records(st.session_state.records, f"Update actual level for {record['id']}")
                    st.session_state.records = store.load_records()
                    st.success("反馈已保存！"); st.rerun()
                else:
                    st.warning("请选择实际降雨等级")

        # 保存分析结果按钮
        if st.session_state.get('analysis_done') and not st.session_state.get('text_saved', False):
            if st.button("💾 保存分析结果"):
                if "本地模式" in mode:
                    ap = st.session_state.archive_path
                    if ap and ap.exists():
                        with open(ap / f"analysis_{st.session_state.record_id}.txt", "w", encoding='utf-8') as f:
                            f.write(st.session_state.raw_text)
                        st.success(f"已保存"); st.session_state.text_saved = True; st.rerun()
                    else:
                        st.error("存档文件夹不存在。")
                else:
                    st.info("云端模式无法保存文本到本地，请手动复制。")
        if st.session_state.get('text_saved'):
            st.info("✅ 分析结果已保存到存档文件夹。")

with tab2:
    st.subheader("📊 历史记录")
    records = st.session_state.records
    if records:
        for rec in records:
            with st.expander(f"{rec['timestamp']} - 预测: {rec['predicted_level']} / 实际: {rec.get('actual_level','未反馈')}"):
                st.write(f"**ID:** {rec['id']}"); st.write(f"**存档文件夹:** {rec['image_folder']}")
                st.write(f"**降雨概率:** {rec['rain_prob']}"); st.write(f"**预测等级:** {rec['predicted_level']}")
                st.write(f"**校正后等级:** {rec['corrected_level']}"); st.text(rec['analysis'])
                if rec['image_folder'].startswith("archive/"):
                    ap = PROJECT_ROOT / rec['image_folder']
                    if ap.exists():
                        st.write("**存档图片：**")
                        cols = st.columns(3)
                        for i, name in enumerate(REQUIRED_NAMES):
                            for ext in ALLOWED_EXTENSIONS:
                                if (ap / f"{name}{ext}").exists():
                                    with cols[i%3]: st.image(str(ap / f"{name}{ext}"), width=150, caption=REQUIRED_DISPLAY[name])
                                    break
        if st.button("📥 导出为 JSON"):
            store.export_to_json(config['data_file_local'])
            with open(config['data_file_local'], 'rb') as f:
                st.download_button("点击下载", f, "analysis_records.json")
        if st.button("🔄 同步到 GitHub"):
            store.save_records(st.session_state.records, "Manual sync"); st.success("已同步")
    else:
        st.info("暂无历史记录。")

with tab3:
    st.subheader("⚖️ 校正管理")
    corr = compute_correction(st.session_state.records)
    st.write(f"当前整体偏差校正因子: **{corr.get('overall_bias',0):.3f}**")
    st.markdown("偏差 = (预测-实际)/预测，校正后等级 = 预测数值×(1-偏差因子)")
    if st.button("🔄 重新计算"): st.rerun()
