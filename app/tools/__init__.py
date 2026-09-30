"""可供 Agent 调用的工具集合。"""
from app.tools.assessment_tools import get_risk_questionnaire, score_risk_profile
from app.tools.product_tools import (
    compare_products,
    get_product_detail,
    search_products,
)

# Agent 可绑定的全部工具
ALL_TOOLS = [
    search_products,
    compare_products,
    get_product_detail,
    get_risk_questionnaire,
    score_risk_profile,
]

__all__ = [
    "ALL_TOOLS",
    "search_products",
    "compare_products",
    "get_product_detail",
    "get_risk_questionnaire",
    "score_risk_profile",
]
