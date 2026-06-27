# 🌧️ 普吉岛降雨图像分析

基于 GitHub Models API (GPT-4o) 的天气预报图像分析工具。通过分析雷达回波图、风速图、模型预报图等6张气象图片，结合用户反馈的实际降雨情况，逐步积累校正系数，提高预测准确性。

---

## 功能

- 上传或选择6张气象图片进行 AI 分析
- 自动记录分析结果，支持用户输入实际降雨反馈
- 基于历史数据计算偏差校正因子，自动修正预测等级
- 历史记录查看、趋势图表展示、数据一键导出
- 支持本地（便携版）和云端（Streamlit Cloud）两种运行模式

---

## 图片命名规则（必须固定）

| 序号 | 文件名 | 说明 |
|------|--------|------|
| 1 | `radar.png` / `radar.jpg` | 雷达回波图 |
| 2 | `wind_3000m.png` / `wind_3000m.jpg` | 3000米高空风速图 |
| 3 | `wind_600m.png` / `wind_600m.jpg` | 600米低空风速图 |
| 4 | `model_mix_1.png` / `model_mix_1.jpg` | 混合模型预报图（第1张） |
| 5 | `model_mix_2.png` / `model_mix_2.jpg` | 混合模型预报图（第2张） |
| 6 | `wind_direction.png` / `wind_direction.jpg` | 风向图 |

支持 `.png`、`.jpg`、`.jpeg` 三种格式。

---

## 本地运行（便携版）

### 前提
- 便携版 Python 环境位于 `I:\portable_python_using`
- 项目已复制到 `I:\portable_python_using\projects\image_weather_analysis\`

### 步骤
1. 在 `.env` 文件中配置你的 GitHub Token 等信息
2. 将6张气象图片放入 `input/` 文件夹
3. 双击 `RUN.bat` 启动
4. 浏览器自动打开 Streamlit 界面

---

## 云端部署（Streamlit Cloud）

### 步骤
1. 将此项目上传到你的 GitHub 仓库（确保 `images/` 文件夹包含图片）
2. 登录 [Streamlit Cloud](https://streamlit.io/cloud)
3. 点击 "New app"，选择该仓库，主文件为 `main.py`
4. 在 app 设置中添加 Secrets：


GITHUB_TOKEN=你的Fine-grained token
GITHUB_REPO_OWNER=你的GitHub用户名
GITHUB_REPO_NAME=image_weather_analysis
AI_MODEL=gpt-4o


5. 部署完成，即可在手机/平板上通过链接访问

---

## 数据说明

- 所有分析记录存储在 `data/analysis_records.json` 文件中
- 数据自动同步到 GitHub 仓库的 `data/analysis_records.json`
- 支持一键导出为 JSON 文件
- 更换 GitHub 账户时，只需 fork 或 clone 仓库，数据随仓库迁移

---

## 校正逻辑

程序基于历史偏差计算整体校正因子：
- **偏差** = （预测数值 - 实际数值）/ 预测数值（预测非零时）
- **校正后等级** = 预测等级数值 × (1 - 整体偏差因子)
- 随着反馈数据增多，校正因子逐渐趋于稳定

---

## 依赖

见 `requirements.txt`，需要在 Streamlit Cloud 上自动安装。本地使用便携版 pip 安装：
