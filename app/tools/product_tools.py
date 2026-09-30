"""金融产品信息工具（Function Calling）。

面向"信息聚合与对比"模块：按条件检索产品、拉取详情、做统一口径对比。
数据来自 app/data/products.py（示例数据）。注意：运行时**默认不再**用它兜底（见
SAMPLE_FALLBACK，默认关闭）——真实数据取不到就如实说查不到；这些工具主要留给测试直接调用。
真实系统应替换为持牌数据供应商 API。
"""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.data.products import (
    RISK_LEVEL_DESC,
    get_by_code,
    query_products,
)

# 统一对比维度（口径一致，便于小白横向比较）
COMPARE_FIELDS = [
    ("category", "产品类型"),
    ("risk_level", "风险等级"),
    ("annual_return", "近1年年化(%)"),
    ("fee", "综合费率(%)"),
    ("min_invest", "起投(元)"),
    ("liquidity", "流动性"),
    ("horizon", "建议持有"),
]


class ProductSearchQuery(BaseModel):
    category: str | None = Field(
        None, description="产品大类，如 货币基金/债券基金/银行理财/大额存单/国债/指数基金ETF/混合基金/股票基金"
    )
    max_risk_level: int | None = Field(
        None, ge=1, le=5, description="可接受的最高风险等级 R1-R5（1 最低，5 最高）"
    )
    keyword: str | None = Field(None, description="关键词，如 定投/红利/短债/活钱")
    limit: int = Field(6, ge=1, le=12, description="返回条数上限")


class ProductCompareQuery(BaseModel):
    codes: list[str] = Field(..., description="要对比的产品代码列表，如 ['IDX001','BOND001']")


class ProductDetailQuery(BaseModel):
    code: str = Field(..., description="产品代码，如 IDX001")


def _slim(p: dict) -> dict:
    """裁剪为对比/展示用的精简字段。"""
    out = {label: p[key] for key, label in COMPARE_FIELDS}
    out["产品代码"] = p["code"]
    out["产品名称"] = p["name"]
    out["风险说明"] = RISK_LEVEL_DESC.get(p["risk_level"], "")
    return out


@tool(args_schema=ProductSearchQuery)
def search_products(
    category: str | None = None,
    max_risk_level: int | None = None,
    keyword: str | None = None,
    limit: int = 6,
) -> dict:
    """按类别 / 最高风险等级 / 关键词检索金融产品，返回统一口径的产品列表（收益从高到低）。"""
    hits = query_products(
        category=category, max_risk_level=max_risk_level, keyword=keyword, limit=limit
    )
    return {
        "count": len(hits),
        "filter": {"category": category, "max_risk_level": max_risk_level, "keyword": keyword},
        "products": [_slim(p) for p in hits],
        "note": "均为演示数据，过往收益不代表未来，不构成投资建议。",
    }


@tool(args_schema=ProductCompareQuery)
def compare_products(codes: list[str]) -> dict:
    """按统一维度对比多个产品（风险/收益/费率/门槛/流动性/持有期）。"""
    rows, missing = [], []
    for code in codes:
        p = get_by_code(code)
        if p:
            rows.append(_slim(p))
        else:
            missing.append(code)
    return {
        "compared": rows,
        "missing": missing,
        "dimensions": [label for _, label in COMPARE_FIELDS],
        "note": "均为演示数据，过往收益不代表未来，不构成投资建议。",
    }


@tool(args_schema=ProductDetailQuery)
def get_product_detail(code: str) -> dict:
    """获取单个产品的完整信息。"""
    p = get_by_code(code)
    if not p:
        return {"found": False, "code": code}
    detail = dict(p)
    detail["risk_desc"] = RISK_LEVEL_DESC.get(p["risk_level"], "")
    detail["found"] = True
    return detail
