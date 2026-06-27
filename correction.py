"""
校正系数计算：加权移动平均偏差。
等级映射：无雨=0, 小雨=1, 中雨=2, 大雨=3, 暴雨=4。
"""
from typing import Dict, List

LEVEL_MAP = {"无雨": 0, "小雨": 1, "中雨": 2, "大雨": 3, "暴雨": 4}
REVERSE_LEVEL_MAP = {0: "无雨", 1: "小雨", 2: "中雨", 3: "大雨", 4: "暴雨"}

def level_to_num(level: str) -> int:
    return LEVEL_MAP.get(level, 0)

def num_to_level(num: float) -> str:
    nearest = round(num)
    nearest = max(0, min(4, nearest))
    return REVERSE_LEVEL_MAP[nearest]

def compute_correction(records: List[Dict]) -> Dict[str, float]:
    valid = [r for r in records if r.get('actual_level') is not None]
    if not valid:
        return {"overall_bias": 0.0}
    total_bias = 0.0
    count = 0
    for r in valid:
        pred = level_to_num(r.get('predicted_level', '无雨'))
        actual = level_to_num(r.get('actual_level', '无雨'))
        if pred != 0:
            bias = (pred - actual) / pred
        else:
            bias = 0 if actual == 0 else -1.0
        total_bias += bias
        count += 1
    overall_bias = total_bias / count if count > 0 else 0.0
    return {"overall_bias": overall_bias}

def apply_correction(predicted_level: str, correction: Dict[str, float]) -> tuple:
    bias = correction.get("overall_bias", 0.0)
    num = level_to_num(predicted_level)
    corrected_num = num * (1 - bias)
    corrected_num = max(0, min(4, corrected_num))
    return num_to_level(corrected_num), bias