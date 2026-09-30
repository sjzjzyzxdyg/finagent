"""可复用的工具调用（ReAct）循环。

给 LLM 绑定一组工具，让它自己决定调哪个工具、传什么参数、要不要再调一次，
最后在同一轮内给出最终答案。撞到步数上限时，去掉工具强制作答，避免把
"让我再查一下"的过场话当成结果返回。product / research 两个 agent 共用它。
mock 模式下模型不产生 tool_calls，第一轮直接返回，不依赖任何外部服务。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.reliability import ainvoke_llm
from app.llm.provider import get_chat_model

_FORCE_ANSWER = "请立即基于以上已获取到的信息给出完整、有条理的最终回答（带来源链接），不要再请求调用工具。"


def _date_prefix() -> str:
    now = datetime.now()
    return (
        f"【当前日期】今天是 {now.year}年{now.month}月{now.day}日。"
        f"涉及'最新/近期/今年/最近'等时间词一律以此为准，联网搜索请使用正确的年份（如 {now.year}年）。\n"
    )


async def run_react(
    system_prompt: str,
    tools: list,
    history: list,
    query: str,
    max_steps: int = 4,
    inject_date: bool = True,
) -> str:
    """跑一轮 ReAct，返回最终答案文本。"""
    # 用流式模型（temperature=0 保证工具决策稳定）：工具决策步的 chunk 内容为空、
    # 前端不显示；最终作答步的正文可逐 token 流式推给前端，与其它 Agent 一致。
    tools_llm = get_chat_model(temperature=0).bind_tools(tools)
    plain_llm = get_chat_model(temperature=0)   # 不带工具，用于强制最终作答
    sys = (_date_prefix() if inject_date else "") + system_prompt
    messages = [SystemMessage(content=sys), *history, HumanMessage(content=query)]
    tmap = {t.name: t for t in tools}

    for _ in range(max_steps):
        ai = await ainvoke_llm(tools_llm, messages)
        messages.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            answer = (getattr(ai, "content", "") or "").strip()
            if answer:
                return answer
            break
        for tc in calls:
            tool = tmap.get(tc.get("name"))
            out = "（未知工具）" if tool is None else await asyncio.to_thread(tool.invoke, tc.get("args") or {})
            messages.append(ToolMessage(content=str(out), tool_call_id=tc.get("id", "")))

    messages.append(HumanMessage(content=_FORCE_ANSWER))
    final = await ainvoke_llm(plain_llm, messages)
    return (getattr(final, "content", "") or "").strip() or "（未能整理出结果，请换个问法或稍后再试。）"
