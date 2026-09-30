"""用 LangGraph StateGraph 编排 Supervisor + 子 Agent 协作网络。

Router 按意图分发到四个业务 Agent（教育 / 产品 / 推荐 / 测评）或闲聊 Agent。
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.assessment_agent import assessment_node
from app.agents.chat_agent import chat_node
from app.agents.education_agent import education_node
from app.agents.holdings_agent import holdings_node
from app.agents.memory_agent import memory_node
from app.agents.multi_agent import multi_node
from app.agents.product_agent import product_node
from app.agents.profile_agent import profile_node
from app.agents.recommend_agent import recommend_node
from app.agents.research_agent import research_node
from app.agents.router import route_intent, router_node
from app.agents.state import AgentState
from app.memory.checkpointer import get_checkpointer


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("memory", memory_node)
    graph.add_node("router", router_node)
    graph.add_node("education", education_node)
    graph.add_node("product", product_node)
    graph.add_node("recommend", recommend_node)
    graph.add_node("assessment", assessment_node)
    graph.add_node("research", research_node)
    graph.add_node("chat", chat_node)
    graph.add_node("profile", profile_node)
    graph.add_node("holding", holdings_node)
    graph.add_node("multi", multi_node)

    graph.add_edge(START, "memory")
    graph.add_edge("memory", "router")
    graph.add_conditional_edges(
        "router",
        route_intent,
        {
            "profile": "profile",
            "holding": "holding",
            "education": "education",
            "product": "product",
            "recommend": "recommend",
            "assessment": "assessment",
            "research": "research",
            "chat": "chat",
            "multi": "multi",
        },
    )
    for node in ("profile", "holding", "education", "product", "recommend",
                 "assessment", "research", "chat", "multi"):
        graph.add_edge(node, END)

    return graph.compile(checkpointer=get_checkpointer())


@lru_cache
def get_graph():
    """编译后的图（单例）。"""
    return build_graph()
