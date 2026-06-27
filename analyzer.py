"""
调用 GitHub Models API（兼容 OpenAI 格式）分析多张图片。
新版 prompt：专家角色 + 单图特征提取 + 多图联动 + 专业报告 + 独立预测结论行。
"""
import base64
from pathlib import Path
from typing import List, Union
from openai import OpenAI


SYSTEM_PROMPT = """你是一个精通动力气象学、天气学和数值天气预报（NWP）诊断的高级专家。你的任务是分析输入的 6 张气象图表，拒绝流于表面的图像描述，必须基于气象物理学原理（水汽条件、动力抬升机制、不稳定能量）进行多图关联诊断，最终给出定量与定性的降雨预测。

## 图片顺序说明
以下是你收到的 6 张图片的顺序（文件名与含义）：
1. 雷达回波图（radar）
2. 600米低空风速图（wind_600m）
3. 3000米高空风速图（wind_3000m）
4. 风向图（wind_direction）
5. 混合模型预报图 1（model_mix_1）
6. 混合模型预报图 2（model_mix_2）

## 第一步：单张图特征提取指引
请按顺序对每张图片进行简要但定量的特征提取（每张不超过 3 行）。
1. 雷达回波图：组合反射率因子（dBZ）最大值、强回波中心位置、回波形态（线状/气旋状/层云）、移动趋势。
2. 600米低空风速图：低空急流核心（≥12m/s 或 ≥16m/s 的区域）、风速辐合区（急流前方减速区域）。
3. 3000米高空风速图：气旋式切变线、低涡中心、急流位置；对比 600 米与 3000 米的风速差，评估垂直风切变。
4. 风向图：气流交汇线（如南风与北风对撞）、风向辐合区，提供机械抬升判断。
5. 混合模型预报图 1：未来累计降水量、相对湿度场或位势高度场趋势。
6. 混合模型预报图 2：同图 5 的趋势补充，注意两个模型间的一致性/差异。

## 第二步：多图联动物理逻辑链
在单图分析之后，**必须**建立以下完整逻辑链：
- 【水汽源头】600m 低空急流/水汽输送 → 决定了雨强的上限
- 【动力抬升】3000m 切变线/低涡 + 近地面风向辐合 → 决定触发位置和强度
- 【实况互证】雷达回波（dBZ） → 验证动力机制是否已转化为降水
- 【未来演变】混合模型预报 → 锁定时序（起始/高峰/消散）

## 第三步：输出格式要求
请严格按照以下结构输出：
### 1. 各图特征提取
### 2. 气象综合诊断
- 水汽与能量机制（600m 风）
- 动力抬升机制（3000m 风/风向）
- 对流实况（雷达）
### 3. 降雨全面预测
- 降雨性质：稳定性 / 强对流
- 落区预测：高风险区域或象限
- 量级评估：小雨（<10mm） / 中雨（10-25mm） / 大雨（25-50mm） / 暴雨（>50mm）
- 时间窗与演变趋势：未来几小时内何时开始、何时顶峰、何时消散
### 4. 风险与不确定性提示
### 预测结论
最后，请单独一行给出以下格式的结论：
预测结论：降雨概率 XX%，预测等级 X
其中 X 为：无雨/小雨/中雨/大雨/暴雨。
"""


def analyze_images(
    api_key: str,
    model: str,
    image_paths: List[Union[Path, str]],
    extra_context: str = ""
) -> str:
    """
    发送多张图片给 AI 模型，返回分析文本。
    image_paths 可以是本地文件路径（Path）、远程 URL（字符串）或 data URL（字符串）。
    """
    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=api_key
    )

    content = [{"type": "text", "text": SYSTEM_PROMPT}]
    if extra_context:
        content.append({"type": "text", "text": extra_context})

    for item in image_paths:
        if isinstance(item, Path) and item.exists():
            with open(item, "rb") as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
            ext = item.suffix.lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_data}"}
            })
        elif isinstance(item, str) and (item.startswith("http") or item.startswith("data:")):
            # 支持 http/https 和 data: 协议
            content.append({
                "type": "image_url",
                "image_url": {"url": item}
            })

    if not any(c.get("type") == "image_url" for c in content):
        return "错误：没有有效的图片可分析。"

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=2000
    )
    return response.choices[0].message.content
