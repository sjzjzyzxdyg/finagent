"""闲聊 / 元问题 Agent。

处理打招呼、一般性问题、以及关于本次对话本身的问题（如"我问了几个问题"）。
只依据对话历史回答，不做知识/产品检索，也不编造数据。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ANSWER_STYLE, AgentState, history_for_prompt, last_human, today_hint
from app.core.reliability import ainvoke_llm
from app.llm.provider import get_chat_model

SYSTEM_PROMPT = (
    "你是友好的个人理财智能助手，面向理财小白。回答用户的打招呼、一般性问题，"
    "或关于本次对话本身的问题（例如'我刚问了几个问题''你能做什么'）。可结合对话历史作答。"
    "你能做的事：讲理财知识、查询与对比理财产品、做风险测评、给个性化配置建议。"
    "不要编造产品或行情数据；若用户其实想学知识/查产品/测风险/要推荐，友好地引导他直接提问对应内容。"
    "【重要】若下方提供了该用户的个人理财资料，说明你**是能看到并可以使用**它的："
    "当用户问'你知道我存了什么吗''结合我的情况'之类问题时，请直接依据该资料回答，"
    "**绝不要**说'我无法访问你的数据/没有权限看到你保存的信息'之类的话。"
    "这份资料是系统在聊天中自动记录的——若为空，可以自然地邀请用户直接说说自己的持仓/目标/每月可投入，"
    "你会帮他记住并在推荐时参考。"
)


async def chat_node(state: AgentState) -> dict:
    query = last_human(state)
    finance = (state.get("user_finance") or "").strip()
    finance_note = (
        f"【该用户的个人理财资料（你可据此回答与其个人情况相关的问题）】：\n{finance}\n\n"
        if finance else ""
    )
    llm = get_chat_model()
    history = history_for_prompt(state, 8)
    prompt = [
        SystemMessage(content=SYSTEM_PROMPT + ANSWER_STYLE + "\n" + today_hint()),
        *history,
        HumanMessage(content=f"{finance_note}用户：{query}"),
    ]
    msg = await ainvoke_llm(llm, prompt)
    return {"messages": [msg], "agent_type": "chat_agent"}
