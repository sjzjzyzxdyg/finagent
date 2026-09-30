"""LangGraph 共享状态定义与通用小工具。"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


# 统一回答风格：让每个 Agent 都"先给结论、再精简展开"，用户一眼能看到答案，不用翻找。
# 各面向用户的 Agent 把它拼进自己的系统提示词即可（chat/education/product/recommend/research）。
ANSWER_STYLE = (
    "\n【回答风格（务必遵守）】"
    "\n1. 开头先用一句话给出**结论/直接答案**（可加粗），让用户一眼看到重点，再展开细节。"
    "\n2. 精简：能一两句说清就不要长篇；需要分点时每点一行、只保留关键信息，不堆砌套话与重复。"
    "\n3. 只答用户问的那一点，去掉无关铺垫和冗长开场；必要的风险提示放最后一行、简短带过即可。"
    "\n4. 除非用户明确要求'详细/展开/深入'，默认给精炼版本；用户追问再补充。"
)


def today_hint() -> str:
    """当前日期提示，注入各 Agent 以获得时间感知（'最近/今年/最新'按此为准）。"""
    n = datetime.now()
    return (
        f"【当前日期】今天是 {n.year}年{n.month}月{n.day}日。"
        f"涉及'最新/近期/今年/最近'等时间词时以此为准（如联网搜索请用 {n.year} 年）。"
    )


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str | None          # 路由目标：单意图为具体意图，多意图为 "multi"
    intents: list | None        # 本轮要依次执行的意图计划（1或2个），多意图编排用
    agent_type: str | None      # 实际处理的子 Agent
    sources: list               # 知识库引用溯源
    risk_profile: str | None    # 用户风险画像（如 "稳健型·R2"）：测评后写入，推荐时读取
    user_finance: str | None    # 用户私有理财资料（持仓/目标等）：由 API 层注入，推荐时读取
    username: str | None        # 当前登录用户名：由 API 层注入，profile_agent 写库时按它隔离
    summary: str | None         # 长对话的滚动摘要（memory 节点维护，压缩较早的历史）
    summary_at: int | None      # 上次生成摘要时的消息条数（用于控制刷新频率）


def last_human(state: AgentState) -> str:
    """取最近一条用户消息内容。"""
    for msg in reversed(state["messages"]):
        if getattr(msg, "type", None) == "human":
            return str(msg.content)
    return ""


def history_for_prompt(state: AgentState, k: int = 8) -> list:
    """构造给 Agent 的对话历史：长对话摘要（若有）作为一条系统提示置顶，再附最近 k 条消息。

    各 Agent 用它替代 `state["messages"][:-1][-k:]`，从而在长对话里也不丢早前上下文。
    """
    from langchain_core.messages import SystemMessage

    msgs = state["messages"][:-1]   # 去掉当前这条（各 Agent 单独作为 query 处理）
    recent = msgs[-k:]
    summary = (state.get("summary") or "").strip()
    if summary:
        return [SystemMessage(content=f"【早前对话摘要（供你了解上下文）】{summary}"), *recent]
    return recent
