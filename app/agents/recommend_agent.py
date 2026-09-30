"""个性化推荐 Agent（挑产品）。

面向"挑产品"模块：结合用户的风险画像（来自测评，存于共享状态）与理财目标，
用产品工具筛出**不超过其风险承受等级**的候选（投资者适当性），
再由 LLM 给出可解释的配置建议 + 风险揭示。绝不推荐超出用户承受力的产品。
"""
from __future__ import annotations

import asyncio
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ANSWER_STYLE, AgentState, history_for_prompt, last_human
from app.config import settings
from app.core.reliability import ainvoke_llm
from app.llm.provider import get_chat_model
from app.tools.live_data import diversify_rows, fetch_live_products
from app.tools.product_tools import search_products
from app.tools.web_tools import web_search_context

SYSTEM_PROMPT = (
    "你是个人理财配置助手，严格遵守投资者适当性：只在用户风险承受等级内推荐产品，绝不推荐超出其风险等级的产品。\n"
    "重要：下面给你的产品是【系统按用户风险等级自动筛选出的可选项】，用来给推荐打底——"
    "它**不是用户自己的持仓，也不是用户建过的候选池**。因此措辞上**绝不要**说'你的候选池''你选的产品''你的候选'，"
    "而应说成'根据你的风险等级，可以考虑以下这几只/几类产品'。\n"
    "请用小白能懂的话给出简单的配置思路（如活钱/稳健/进取各放多少比例），并逐条解释推荐理由。\n"
    "若用户问'怎么买/在哪买'，简要说明正规购买渠道：银行手机银行、券商 APP、基金公司官方渠道、"
    "以及持牌第三方代销平台（如支付宝、天天基金等），并提醒认准持牌正规渠道、不要轻信'高收益保本'的私下推销。\n"
    "必须包含明显的风险揭示：过往收益不代表未来、可能亏损、需预留应急资金；并说明这不构成投资建议。"
    "若用户尚未做风险测评，先温和建议其完成测评以获得更贴合的方案。"
    "若给了【联网市场信息】，结合它补充当前市场背景/趋势并给出来源链接；产品数字仍以给定数据为准，不要编造。"
    "若给了【个人理财资料】（用户填写的持仓/资产/目标/月投入等），务必据此做个性化："
    "避免与其已有持仓过度重复、结合其目标与月投入给出具体的金额或比例安排；没有该资料时给通用建议即可。"
)

_DEFAULT_PROFILE = "稳健型·R2（默认：尚未测评）"


def _max_risk_from_profile(profile: str | None) -> int:
    if profile:
        m = re.search(r"[Rr]([1-5])", profile)
        if m:
            return int(m.group(1))
    return 2  # 未测评时保守默认


async def recommend_node(state: AgentState) -> dict:
    query = last_human(state)
    profile = state.get("risk_profile")
    max_risk = _max_risk_from_profile(profile)
    profile_label = profile or _DEFAULT_PROFILE

    # 适当性候选池 + 联网市场信息：两件网络慢活（akshare 全市场排行、联网搜索）互不依赖，
    # 并行取数（gather）而非串行 await，减少出字前的等待、缩短"思考时间"。
    # 多取一些(24)再去同质化到 8：避免推荐清一色同类型/同公司/同一只基金的不同份额
    (live_rows, source), web = await asyncio.gather(
        asyncio.to_thread(fetch_live_products, "", max_risk, 24),
        asyncio.to_thread(web_search_context, query),
    )
    if live_rows:
        live_rows = diversify_rows(live_rows, 8)
        candidates = {"数据来源": f"{source}(实时)", "products": live_rows}
    elif settings.sample_fallback:   # 仅当显式开启才用内置示例库兜底（默认关闭）
        res = search_products.invoke(
            {"category": None, "max_risk_level": max_risk, "keyword": None, "limit": 8}
        )
        candidates = {"数据来源": "内置示例库", "products": res.get("products", res)}
    else:                            # 运行时不兜底：没有实时产品就给空列表，只讲配置思路、不虚构产品
        candidates = {"数据来源": "无实时数据", "products": []}
    web_note = f"\n【联网检索到的市场信息】\n{web}\n" if web else ""
    # 无具体候选产品时，明确要求模型只给配置思路/比例、不要编造产品名与数字
    no_data_note = (
        "" if candidates["products"]
        else "\n【注意】当前未取到实时候选产品，请只给出资产配置思路与各类占比建议，"
             "不要虚构任何具体产品名称、代码或收益数字。\n"
    )

    # 个人理财资料（私有，由 API 层注入）：用于个性化
    finance = (state.get("user_finance") or "").strip()
    finance_note = f"\n【该用户填写的个人理财资料（私有，用于个性化）】：{finance}\n" if finance else ""

    llm = get_chat_model()
    history = history_for_prompt(state, 8)
    prompt = [
        SystemMessage(content=SYSTEM_PROMPT + ANSWER_STYLE),
        *history,
        HumanMessage(
            content=(
                f"用户风险画像：{profile_label}（可承受最高风险等级 R{max_risk}）\n"
                f"【系统按该风险等级自动筛选出的可选产品，供推荐参考，不是用户已持有/已选的】：{candidates}\n"
                f"{finance_note}"
                f"{web_note}"
                f"{no_data_note}"
                f"用户需求：{query}\n"
                f"请据此给出可解释的配置建议（记得：这些是系统筛的可选项，不要称之为'用户的候选池'）。"
            )
        ),
    ]
    msg = await ainvoke_llm(llm, prompt)
    return {"messages": [msg], "agent_type": "recommend_agent"}
