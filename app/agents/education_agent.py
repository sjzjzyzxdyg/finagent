"""投资者教育 Agent（学知识）。

面向"学知识"模块：用小白听得懂的话解释理财概念、原理、名词。
**直接依据大模型自身知识作答（已去除 RAG 检索链路）**——对通用理财概念，
现代大模型的内建知识足够可靠，省掉了"检索→打分→改写"的额外调用与向量库/embedding 依赖。
具体产品数字、最新行情由"产品/研究"Agent 用真实数据负责，本 Agent 不编造任何具体数字。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ANSWER_STYLE, AgentState, history_for_prompt, last_human, today_hint
from app.core.reliability import ainvoke_llm
from app.llm.provider import get_chat_model

SYSTEM_PROMPT = (
    "你是一名耐心的个人理财教育助手，面向没有金融背景的普通人。"
    "用通俗、口语化的方式解释理财概念、原理、名词：多打比方、少用术语，必要的术语顺带解释清楚。"
    "只讲**通用知识**，绝不编造具体的产品名称、收益率、费率、净值等数字——这些属于实时数据，"
    "应由'产品/研究'功能给出；被问到时可引导用户去查询具体产品。"
    "涉及风险要客观提示、不夸大收益；给出方法类内容时提醒'不构成投资建议'。"
    "若用户其实是想查具体产品、做风险测评或要个性化推荐，友好地引导他直接提出对应需求。"
)


async def education_node(state: AgentState) -> dict:
    query = last_human(state)
    llm = get_chat_model()
    history = history_for_prompt(state, 8)
    prompt = [
        SystemMessage(content=SYSTEM_PROMPT + ANSWER_STYLE + "\n" + today_hint()),
        *history,
        HumanMessage(content=query),
    ]
    msg = await ainvoke_llm(llm, prompt)
    return {"messages": [msg], "agent_type": "education_agent", "sources": []}
