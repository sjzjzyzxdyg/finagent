"""风险测评工具（投资者适当性）。

面向"风险测评"模块：提供标准问卷、把用户作答打分为风险画像（谨慎/稳健/平衡/进取/激进）
及可承受的最高风险等级 R1-R5。真实系统的问卷与评分口径需符合监管适当性管理要求。
"""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# 标准风险测评问卷：每题选项按风险偏好从低到高计 1-4 分
QUESTIONNAIRE: list[dict] = [
    {
        "q": "1. 您的年龄段？",
        "options": ["A. 60岁以上", "B. 45-60岁", "C. 30-45岁", "D. 30岁以下"],
    },
    {
        "q": "2. 您计划的投资期限？",
        "options": ["A. 1年以内", "B. 1-3年", "C. 3-5年", "D. 5年以上"],
    },
    {
        "q": "3. 这笔投资资金占您总资产的比例？",
        "options": ["A. 几乎全部", "B. 大部分", "C. 一半左右", "D. 一小部分"],
    },
    {
        "q": "4. 您的投资经验？",
        "options": ["A. 几乎没有", "B. 只买过存款/货基", "C. 买过基金/理财", "D. 有股票等经验"],
    },
    {
        "q": "5. 如果投资一个月内亏损 10%，您会？",
        "options": ["A. 非常焦虑，立刻赎回", "B. 有些担心，考虑减仓", "C. 能接受，继续持有", "D. 视为加仓机会"],
    },
    {
        "q": "6. 您更认同哪种理财目标？",
        "options": ["A. 绝对保本", "B. 跑赢通胀即可", "C. 稳健增值", "D. 追求较高回报，可承受波动"],
    },
]

# 画像与可承受最高风险等级
_PROFILES = [
    (0.00, "谨慎型", 1, ["货币基金", "大额存单", "国债"]),
    (0.35, "稳健型", 2, ["货币基金", "债券基金", "银行理财", "国债"]),
    (0.55, "平衡型", 3, ["债券基金", "指数基金ETF", "混合基金"]),
    (0.75, "进取型", 4, ["指数基金ETF", "混合基金"]),
    (0.90, "激进型", 5, ["混合基金", "股票基金"]),
]

_LETTER_SCORE = {"A": 1, "B": 2, "C": 3, "D": 4, "a": 1, "b": 2, "c": 3, "d": 4}


class ScoreQuery(BaseModel):
    answers: list[str] = Field(
        ...,
        description="每题所选选项字母，如 ['A','B','C','C','B','C']；长度应等于问卷题数",
    )


def score_answers(answers: list[str]) -> dict:
    """把作答（每题 A/B/C/D）打分为风险画像。纯函数，供 Agent 与测试直接调用。"""
    n = len(QUESTIONNAIRE)
    got = [_LETTER_SCORE.get(str(a).strip()[:1], 0) for a in answers[:n]]
    if not got or all(s == 0 for s in got):
        return {"ok": False, "reason": "未识别到有效作答（每题请给 A/B/C/D）。"}
    raw = sum(got)
    max_score = 4 * n
    ratio = raw / max_score
    profile_name, max_risk, cats = _PROFILES[0][1:]
    for threshold, name, mr, categories in _PROFILES:
        if ratio >= threshold:
            profile_name, max_risk, cats = name, mr, categories
    return {
        "ok": True,
        "raw_score": raw,
        "max_score": max_score,
        "ratio": round(ratio, 2),
        "profile": profile_name,
        "max_risk_level": max_risk,
        "risk_tag": f"{profile_name}·R{max_risk}",
        "suitable_categories": cats,
        "note": "该结果用于投资者适当性匹配；您可选择的产品风险等级不应超过 R%d。" % max_risk,
    }


@tool
def get_risk_questionnaire() -> dict:
    """返回标准风险测评问卷（题目与选项），供用户作答。"""
    return {"questions": QUESTIONNAIRE, "total": len(QUESTIONNAIRE)}


@tool(args_schema=ScoreQuery)
def score_risk_profile(answers: list[str]) -> dict:
    """根据用户对风险测评问卷的作答，计算风险画像与可承受的最高风险等级 R1-R5。"""
    return score_answers(answers)
