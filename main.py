"""
Streamlit 主程序。同时适配本地（便携版）和云端（Streamlit Cloud）。
包含三个标签页：分析、历史记录、校正管理。
每次分析自动归档图片，并可手动保存 AI 分析文本。
批量导入图片自动保存到本地 images/ 和 GitHub 仓库 images/。
一键保存功能：同时保存实际降雨等级和分析文本。
历史记录每条可单独删除（红色按钮在右侧），删除后自动同步 GitHub。
同步归档时支持自定义文件夹名称（默认使用分析时间），自动上传 JSON 和 6 张图片。
"""
import streamlit as st
from pathlib import Path
import sys
import os
import shutil
import base64
import json
from datetime import datetime
import plotly.express as px
import pandas as pd
from PIL import Image
import io
import re
import requests

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
mode = st.sidebar.radio("运行模式",
    ["云端模式 (读取仓库 images/ 文件夹)", "本地模式 (读取本地 input/ 文件夹)"], index=0)
st.sidebar.markdown("---")
st.sidebar.markdown("**🔑 GitHub 凭据**")
api_key_input = st.sidebar.text_input("GitHub Token", type="password", value=config.get('token', ''),
    help="在 https://github.com/settings/tokens 生成（需repo和models权限）")
owner_input = st.sidebar.text_input("仓库所有者 (Owner)", value=config.get('owner', ''), help="你的 GitHub 用户名")
repo_input = st.sidebar.text_input("仓库名称 (Repo)", value=config.get('repo', ''), help="例如 image_weather_analysis")
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
try:
    if 'records' not in st.session_state:
        st.session_state.records = store.load_records()
    else:
        loaded = store.load_records()
        if loaded:
            st.session_state.records = loaded
except Exception:
    if 'records' not in st.session_state:
        st.session_state.records = []

st.sidebar.markdown("---")
st.sidebar.markdown("**📷 需要的6张图片（文件名必须完全匹配）：**")
for key in ["radar", "wind_600m", "wind_3000m", "wind_direction", "model_mix_1", "model_mix_2"]:
    st.sidebar.text(f"- {key}.png/.jpg/.jpeg")
if "云端模式" in mode:
    st.sidebar.info("✅ 请确保仓库根目录下的 `images/` 文件夹包含上述6张图片。\n上传的图片会自动同步到仓库。")

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

def find_standard_images_multiple_sources():
    """从 images/, input/, _uploads/ 中查找标准图片，返回 {name: Path} 字典"""
    all_results = {}
    sources = [
        PROJECT_ROOT / "images",
        config['images_local_dir'],
        config['images_local_dir'] / "_uploads"
    ]
    for src_dir in sources:
        if src_dir.exists():
            found = find_images_in_dir(src_dir)
            for name, path in found.items():
                if name not in all_results:
                    all_results[name] = path
    return all_results

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

def upload_to_github(file_bytes: bytes, remote_path: str, commit_msg: str = "Upload image"):
    if not github_api or not github_api.token:
        return False
    try:
        existing = github_api.get_file_content(remote_path)
        sha = existing['sha'] if existing else ''
    except Exception:
        sha = ''
    b64_content = base64.b64encode(file_bytes).decode('utf-8')
    url = f"https://api.github.com/repos/{config['owner']}/{config['repo']}/contents/{remote_path}"
    headers = {
        "Authorization": f"token {github_api.token}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {"message": commit_msg, "content": b64_content}
    if sha:
        payload['sha'] = sha
    try:
        resp = requests.put(url, headers=headers, json=payload)
        return resp.status_code in (200, 201)
    except:
        return False

def safe_folder_name(name: str) -> str:
    """将用户输入的字符串转为安全的文件夹名（替换特殊字符）"""
    # 替换空格、冒号、斜杠等为下划线
    safe = re.sub(r'[\\/:*?"<>| ]', '_', name)
    # 去除前导后缀空格和下划线
    safe = safe.strip('_ ')
    # 如果结果为空，使用时间戳
    if not safe:
        safe = datetime.now().strftime("%Y%m%d%H%M%S")
    return safe

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

st.title("🌧️ 普吉岛天气预报图像分析")
st.markdown("上传或选择6张标准气象图，让 AI 分析降雨情况并记录校正。")

tab1, tab2, tab3 = st.tabs(["📤 分析", "📊 历史记录", "⚖️ 校正管理"])

with tab1:
    col_left, col_right = st.columns([1, 1.5])
    with col_left:
        st.subheader("图片准备")
        with st.expander("📂 批量导入图片（自动识别类型并保存到 images/）"):
            st.markdown("上传多张图片，程序将根据文件名自动匹配类型，您也可以手动调整。上传后自动保存到本地 images/ 和 GitHub 仓库 images/（需配置凭据）。")
            uploaded_files_batch = st.file_uploader("选择图片文件（可多选）", type=['png','jpg','jpeg'], accept_multiple_files=True, key="batch_uploader")
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
                        local_input = config['images_local_dir'] / target_name
                        local_input.parent.mkdir(parents=True, exist_ok=True)
                        with open(local_input, "wb") as f:
                            f.write(file_bytes)
                        local_images = PROJECT_ROOT / "images" / target_name
                        local_images.parent.mkdir(parents=True, exist_ok=True)
                        with open(local_images, "wb") as f:
                            f.write(file_bytes)
                        if github_api and github_api.token:
                            remote_path = f"images/{target_name}"
                            if not upload_to_github(file_bytes, remote_path, f"Upload {target_name}"):
                                error_files.append(f"{uf.name} (GitHub上传失败)")
                        success_count += 1
                    if error_files:
                        st.warning(f"以下文件有问题：{', '.join(error_files)}")
                    st.success(f"成功导入 {success_count} 张图片到本地和 GitHub images/ 文件夹！")
                    st.session_state.batch_mapping = {}
                    st.rerun()
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
        else:
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
        extra = st.text_area("分析时间（例如：明天下午3点）", placeholder="输入你想要预测的具体时间，AI将据此分析降雨")

    with col_right:
        st.subheader("🤖 AI 分析")
        if st.button("🚀 开始分析", type="primary"):
            timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
            archive_path = ARCHIVE_DIR / timestamp_str
            image_paths = []
            uploaded = st.session_state.get('batch_uploader', [])
            if uploaded and len(uploaded) > 0:
                for uf in uploaded:
                    if uf is not None:
                        img_bytes = uf.getbuffer()
                        b64 = base64.b64encode(img_bytes).decode('utf-8')
                        ext = Path(uf.name).suffix.lower()
                        mime = "image/png" if ext == '.png' else "image/jpeg"
                        image_paths.append(f"data:{mime};base64,{b64}")
                uploads_dir = config['images_local_dir'] / "_uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                for uf in uploaded:
                    if uf is not None:
                        with open(uploads_dir / uf.name, "wb") as f:
                            f.write(uf.getbuffer())
                archive_images(config['images_local_dir'], archive_path)
            else:
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
                else:
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
            st.session_state.rain_prob = rain_prob
            st.session_state.pred_level = pred_level
            st.session_state.result_text = result_text
            correction = compute_correction(st.session_state.records)
            corrected_level = pred_level; bias = 0.0
            if pred_level and pred_level in REVERSE_LEVEL_MAP.values():
                corrected_level, bias = apply_correction(pred_level, correction)
            st.session_state.corrected_level = corrected_level
            st.session_state.bias = bias
            now = datetime.now()
            # ===== 重要修改：将分析时间（extra）存入记录 =====
            record = {
                'id': now.strftime("%Y%m%d%H%M%S"),
                'timestamp': now.isoformat(),
                'analyze_time': extra if extra else "未指定",  # 新增字段
                'image_folder': f"archive/{timestamp_str}" if "本地模式" in mode else config['images_remote_dir'],
                'rain_prob': rain_prob, 'predicted_level': pred_level,
                'corrected_level': corrected_level, 'analysis': result_text,
                'raw_response': result_text, 'image_count': len(image_paths), 'actual_level': None
            }
            st.session_state.last_prediction = record
            st.session_state.archive_path = archive_path if "本地模式" in mode else None
            st.session_state.record_id = record['id']
            st.session_state.text_saved = False
            try:
                store.add_record(record)
                st.session_state.records = store.load_records()
            except Exception as e:
                st.session_state.records.append(record)
                st.warning(f"记录保存到外部存储时出现问题：{e}，但内存中已保留。")
            st.session_state.analysis_done = True
            st.success("✅ 分析完成！请查看下方结果并保存反馈。")
            st.rerun()
        if st.session_state.get('analysis_done'):
            st.write("**AI 原始回复：**")
            st.text(st.session_state.result_text)
            st.write(f"**降雨概率：** {st.session_state.rain_prob}")
            st.write(f"**AI 预测等级：** {st.session_state.pred_level}")
            st.write(f"**校正后等级：** {st.session_state.corrected_level} (偏差因子: {st.session_state.bias:.2f})")
            st.divider()
            st.subheader("💾 保存本次分析")
            actual_level = st.selectbox("实际降雨等级", ["", "无雨","小雨","中雨","大雨","暴雨"], key="actual_select")
            if st.button("一键保存（等级 + 分析文本）"):
                errs = []
                if actual_level:
                    found = False
                    for r in st.session_state.records:
                        if r['id'] == st.session_state.record_id:
                            r['actual_level'] = actual_level
                            found = True
                            break
                    if found:
                        try:
                            store.save_records(st.session_state.records, f"Update actual level for {st.session_state.record_id}")
                        except Exception as e:
                            errs.append(f"数据库保存失败: {e}")
                    else:
                        errs.append("未找到当前记录")
                else:
                    errs.append("未选择实际降雨等级")
                if not st.session_state.get('text_saved'):
                    if "本地模式" in mode:
                        ap = st.session_state.archive_path
                        if ap and ap.exists():
                            try:
                                with open(ap / f"analysis_{st.session_state.record_id}.txt", "w", encoding='utf-8') as f:
                                    f.write(st.session_state.result_text)
                                st.session_state.text_saved = True
                            except Exception as e:
                                errs.append(f"文本保存失败: {e}")
                        else:
                            errs.append("存档文件夹不存在")
                    else:
                        st.info("云端模式无法保存文本到本地，请手动复制")
                if errs:
                    st.error("部分操作未完成：\n" + "\n".join(errs))
                else:
                    st.success("✅ 本次分析已完整保存（等级 + 文本）！")
                    st.rerun()

with tab2:
    st.subheader("📊 历史记录")
    records = st.session_state.records
    if records:
        for idx, rec in enumerate(records):
            cols = st.columns([5, 1])
            with cols[0]:
                expander_key = f"rec_{rec.get('id', idx)}"
                # 在标题中增加分析时间显示
                analyze_time = rec.get('analyze_time', '未知')
                with st.expander(
                    f"{rec['timestamp']} - 分析时间: {analyze_time} - 预测: {rec['predicted_level']} / 实际: {rec.get('actual_level','未反馈')}",
                    key=expander_key
                ):
                    st.write(f"**ID:** {rec['id']}")
                    st.write(f"**分析时间:** {analyze_time}")
                    st.write(f"**存档文件夹:** {rec['image_folder']}")
                    st.write(f"**降雨概率:** {rec['rain_prob']}")
                    st.write(f"**预测等级:** {rec['predicted_level']}")
                    st.write(f"**校正后等级:** {rec['corrected_level']}")
                    st.write(f"**实际等级:** {rec.get('actual_level', '未反馈')}")
                    st.text(rec['analysis'])
                    if rec['image_folder'].startswith("archive/"):
                        ap = PROJECT_ROOT / rec['image_folder']
                        if ap.exists():
                            st.write("**存档图片：**")
                            local_cols = st.columns(3)
                            for i, name in enumerate(REQUIRED_NAMES):
                                for ext in ALLOWED_EXTENSIONS:
                                    if (ap / f"{name}{ext}").exists():
                                        with local_cols[i%3]:
                                            st.image(str(ap / f"{name}{ext}"), width=150, caption=REQUIRED_DISPLAY[name])
                                        break
            with cols[1]:
                del_btn_key = f"del_{rec.get('id', idx)}"
                if st.button("🗑️", key=del_btn_key, help="删除此条"):
                    st.session_state.records = [r for r in st.session_state.records if r.get('id') != rec.get('id')]
                    try:
                        store.save_records(st.session_state.records, f"Delete record {rec.get('id', '')}")
                        st.success("已删除并同步！")
                    except Exception as e:
                        st.warning(f"删除成功但同步失败: {e}")
                    st.rerun()

        # ---------- 同步并归档到 GitHub（可自定义文件夹名）----------
        st.markdown("---")
        st.subheader("📤 同步并归档到 GitHub")

        # 从最近一条记录获取分析时间作为默认文件夹名
        last_analyze_time = ""
        if records:
            last_analyze_time = records[-1].get('analyze_time', '')
        if not last_analyze_time:
            last_analyze_time = datetime.now().strftime("%Y年%m月%d日 %H点%M分")

        archive_folder_name = st.text_input(
            "归档文件夹名称（可修改）",
            value=last_analyze_time,
            help="输入有意义的名称，例如：6月27日14点预报"
        )
        safe_name = safe_folder_name(archive_folder_name)

        st.markdown(f"最终文件夹名: `history_records/{safe_name}/`")

        if st.button("🚀 同步并归档（上传数据+图片 → 清理本地图片）"):
            if not github_api or not github_api.token:
                st.error("请先在侧边栏配置 GitHub 凭据。")
            else:
                base_remote_path = f"history_records/{safe_name}"
                errors = []
                success_msgs = []

                # 1. 上传 JSON 记录
                json_content = json.dumps(st.session_state.records, ensure_ascii=False, indent=2)
                json_remote = f"{base_remote_path}/analysis_records.json"
                try:
                    existing_json = github_api.get_file_content(json_remote)
                    sha_json = existing_json['sha'] if existing_json else ''
                    github_api.update_file(json_remote, json_content, sha_json, f"Archive {safe_name}")
                    success_msgs.append(f"✅ JSON 已上传")
                except Exception as e:
                    errors.append(f"上传 JSON 失败: {e}")

                # 2. 查找所有标准图片
                image_dict = find_standard_images_multiple_sources()
                if len(image_dict) < 6:
                    missing_names = [n for n in REQUIRED_NAMES if n not in image_dict]
                    errors.append(f"仅找到 {len(image_dict)}/6 张图片，缺少: {', '.join(missing_names)}。请先通过批量导入或手动放置。")
                else:
                    uploaded_count = 0
                    for name in REQUIRED_NAMES:
                        if name not in image_dict:
                            errors.append(f"本地未找到图片: {name}")
                            continue
                        img_path = image_dict[name]
                        remote_img_path = f"{base_remote_path}/images/{img_path.name}"
                        try:
                            with open(img_path, "rb") as f:
                                img_bytes = f.read()
                            if upload_to_github(img_bytes, remote_img_path, f"Upload {img_path.name}"):
                                uploaded_count += 1
                                success_msgs.append(f"✅ {img_path.name} 已上传")
                            else:
                                errors.append(f"上传 {img_path.name} 失败（GitHub API 返回错误）")
                        except Exception as e:
                            errors.append(f"上传 {img_path.name} 异常: {e}")

                    if uploaded_count == 6:
                        deleted_count = 0
                        # 删除 images/
                        images_dir = PROJECT_ROOT / "images"
                        for name in REQUIRED_NAMES:
                            for ext in ALLOWED_EXTENSIONS:
                                p = images_dir / f"{name}{ext}"
                                if p.exists():
                                    try:
                                        p.unlink()
                                        deleted_count += 1
                                        break
                                    except: pass
                        # 删除 input/ 和 _uploads
                        input_dir = config['images_local_dir']
                        for name in REQUIRED_NAMES:
                            for ext in ALLOWED_EXTENSIONS:
                                p = input_dir / f"{name}{ext}"
                                if p.exists():
                                    try:
                                        p.unlink()
                                        break
                                    except: pass
                        uploads_dir = input_dir / "_uploads"
                        if uploads_dir.exists():
                            for f in uploads_dir.iterdir():
                                try: f.unlink()
                                except: pass
                        success_msgs.append(f"✅ 已清理本地图片。")
                    else:
                        errors.append(f"图片上传不完整（{uploaded_count}/6），未删除本地图片。")

                for msg in success_msgs:
                    st.success(msg)
                if errors:
                    st.error("部分操作异常：\n" + "\n".join(errors))
                if not errors:
                    st.success("🎉 全部操作完成！")
                st.rerun()

        # ---------- 原有批量操作按钮 ----------
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("📥 导出为 JSON"):
                store.export_to_json(config['data_file_local'])
                with open(config['data_file_local'], 'rb') as f:
                    st.download_button("点击下载", f, "analysis_records.json")
        with col2:
            if st.button("🔄 同步到 GitHub (仅数据)"):
                store.save_records(st.session_state.records, "Manual sync")
                st.success("已同步到 GitHub 仓库！")
        with col3:
            if st.button("🔄 重新加载记录"):
                st.session_state.records = store.load_records()
                st.rerun()
    else:
        st.info("暂无历史记录。")

with tab3:
    st.subheader("⚖️ 校正管理")
    corr = compute_correction(st.session_state.records)
    st.write(f"当前整体偏差校正因子: **{corr.get('overall_bias',0):.3f}**")
    st.markdown("偏差 = (预测-实际)/预测，校正后等级 = 预测数值×(1-偏差因子)")
    if st.button("🔄 重新计算"):
        st.rerun()
