"""风险测评 Agent（投资者适当性）。

面向"测风险"模块：
- 若用户消息里包含成套作答（如 "A B C C B C" 或 "1.A 2.B ..."），直接打分为风险画像，
  并把画像写入共享状态，供后续"推荐"Agent 做适当性匹配；
- 否则，把标准问卷呈现给用户，引导其作答。
"""
from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import AgentState, last_human
from app.core.reliability import ainvoke_llm
from app.db import repository as repo
from app.llm.provider import get_chat_model
from app.tools.assessment_tools import (
    QUESTIONNAIRE,
    get_risk_questionnaire,
    score_answers,
)

SYSTEM_PROMPT = (
    "你是投资者风险测评助手。已根据用户作答算出风险画像，请用鼓励、易懂的语气告诉用户："
    "其风险类型、可承受的最高风险等级（R几）意味着什么、以及适合关注哪几类产品，"
    "并提醒这是投资者适当性的基础、买产品时风险等级不应超过该等级。不要编造未提供的数字。"
)


def _extract_answers(text: str) -> list[str]:
    """从自由文本里抽取每题作答字母。支持 'A B C C B C' / '1A2B3C' / 'A、B、C' 等。"""
    letters = re.findall(r"[A-Da-d]", text)
    return [ch.upper() for ch in letters]


async def assessment_node(state: AgentState) -> dict:
    query = last_human(state)
    answers = _extract_answers(query)

    # 作答数量达到题数一半以上，视为在提交测评
    if len(answers) >= max(3, len(QUESTIONNAIRE) // 2):
        result = score_answers(answers)
        if result.get("ok"):
            llm = get_chat_model()
            prompt = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"测评结果：{result}\n用户原话：{query}\n请给出友好的解读。"
                    )
                ),
            ]
            msg = await ainvoke_llm(llm, prompt)
            # 写入共享状态（本会话即时可用）+ 持久化到用户资料（跨会话可用）
            await repo.set_user_risk(state.get("username") or "anonymous", result["risk_tag"])
            return {
                "messages": [msg],
                "agent_type": "assessment_agent",
                "risk_profile": result["risk_tag"],
            }

    # 未作答或无法识别 → 呈现问卷
    q = get_risk_questionnaire.invoke({})
    lines = ["📋 风险承受能力测评（共 %d 题，请依次回复每题选项，如：A B C C B C）\n" % q["total"]]
    for item in q["questions"]:
        lines.append(item["q"])
        lines.append("　　" + "　".join(item["options"]))
        lines.append("")
    lines.append("完成后我会算出你的风险画像（谨慎/稳健/平衡/进取/激进），并据此为你匹配合适的产品类型。")
    return {
        "messages": [AIMessage(content="\n".join(lines))],
        "agent_type": "assessment_agent",
    }
