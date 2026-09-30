"""我的持仓 / 收益查询 Agent（查"我买的产品现在怎么样"）。

区别于 recommend（给配置建议）：这里是**读取用户已保存的持仓资料**，把其中的基金代码
逐个用实时接口查最新净值 / 分段收益，汇总成"你的持仓现状"。

- 持仓资料从 state.user_finance 读（API 层注入；被动捕捉/主动更新写入）。
- 资料里若含 6 位基金代码，就并行查每只的最新数据；没有代码就如实说明并请用户补充代码。
- 只用查到的真实数据说话，不编造收益数字。
"""
from __future__ import annotations

import asyncio
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import ANSWER_STYLE, AgentState, last_human
from app.core.reliability import ainvoke_llm
from app.llm.provider import get_chat_model
from app.tools.live_data import fund_detail_text

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")   # 从持仓文本里抽 6 位基金代码

SYSTEM_PROMPT = (
    "你是理财助手，帮用户了解他**自己持仓**的最新现状与收益。下面给你：他保存的持仓资料，"
    "以及系统按其中基金代码查到的最新数据（净值 / 分段回报）。请据此汇总成清晰的『你的持仓现状』：\n"
    "- 有实时数据的，用数据说话（最新净值、近1月/3月/半年/1年等区间收益）；\n"
    "- 资料里没有基金代码、或某只查不到数据的，如实说明，并提示用户补充准确的基金代码后可再查；\n"
    "- 绝不编造任何收益 / 净值数字；必要时提示过往收益不代表未来、不构成投资建议。"
)


async def holdings_node(state: AgentState) -> dict:
    finance = (state.get("user_finance") or "").strip()
    query = last_human(state)

    if not finance:
        reply = (
            "我这边还没有你的持仓记录～你可以直接告诉我持有哪些（**最好带上基金代码**，"
            "例如「我持有华夏XX 005273 五万、某短债 019112 三万」），我记下来后就能帮你查每只的最新收益近况了。"
        )
        return {"messages": [AIMessage(content=reply)], "agent_type": "holdings_agent"}

    codes = list(dict.fromkeys(_CODE_RE.findall(finance)))[:8]   # 去重、最多查 8 只
    blocks: list[str] = []
    if codes:
        results = await asyncio.gather(*[asyncio.to_thread(fund_detail_text, c) for c in codes])
        blocks = [d for d in results if d]

    if blocks:
        data_note = "\n\n".join(blocks)
    elif codes:
        data_note = f"（持仓含代码 {'、'.join(codes)}，但暂未取到实时数据，可稍后再试。）"
    else:
        data_note = "（持仓资料里没有基金代码，无法查具体收益，请补充准确代码。）"

    llm = get_chat_model()
    prompt = [
        SystemMessage(content=SYSTEM_PROMPT + ANSWER_STYLE),
        HumanMessage(content=(
            f"【用户保存的持仓资料】\n{finance}\n\n"
            f"【系统按代码查到的最新数据】\n{data_note}\n\n"
            f"用户问题：{query}\n"
            f"请汇总他持仓的最新收益/现状。"
        )),
    ]
    msg = await ainvoke_llm(llm, prompt)
    return {"messages": [msg], "agent_type": "holdings_agent"}
