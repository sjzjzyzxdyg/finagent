"""多意图编排 Agent。

一个问题同时涉及多个诉求时（当前覆盖最常见的"先学后挑/看"组合：教育 + 推荐/产品），
按计划依次执行相关子 Agent，再把各自回答合并成一条有分段的答复。

计划由 router 生成并放在 state["intents"]（如 ["education", "recommend"]）。
子 Agent 各自读 state（用户问题 / 风险画像 / 理财资料），互不依赖，可独立执行后拼接。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage

from app.agents.education_agent import education_node
from app.agents.product_agent import product_node
from app.agents.recommend_agent import recommend_node
from app.agents.state import AgentState

_NODES = {
    "education": education_node,
    "product": product_node,
    "recommend": recommend_node,
}


async def multi_node(state: AgentState) -> dict:
    plan = state.get("intents") or []
    parts: list[str] = []
    sources: list = []
    for intent in plan:
        node = _NODES.get(intent)
        if node is None:
            continue
        out = await node(state)
        msgs = out.get("messages") or []
        content = getattr(msgs[-1], "content", "") if msgs else ""
        if content:
            parts.append(content.strip())
        sources += out.get("sources") or []

    combined = "\n\n———\n\n".join(p for p in parts if p) or "（未能生成回答，请换个问法再试。）"
    return {"messages": [AIMessage(content=combined)], "agent_type": "multi_agent", "sources": sources}
