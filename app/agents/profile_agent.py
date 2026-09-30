"""个人理财资料更新 Agent（在对话中直接改「我的理财数据」）。

当用户在聊天里说"更新我的理财数据…""记一下我又买了…""把我的持仓改成…"等，
这个节点把更新指令与已存资料**智能合并**（保留仍有效的旧信息，按指令增/删/改），
保存回该用户的私有资料，并回一条确认。

资料按用户名隔离；写库需要用户名，由 API 层注入到 state.username。
合并用非流式的工具模型（temperature=0，稳定），其过程不流给前端。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import AgentState, last_human
from app.core.reliability import ainvoke_llm
from app.db import repository as repo
from app.llm.provider import get_utility_model
from app.tools.live_data import enrich_codes_in_text

_MERGE_PROMPT = (
    "你是理财资料整理助手。用户有一段已保存的【个人理财资料】，现在给出一条【更新指令】。"
    "请把更新合并进去，输出更新后的**完整**资料：保留仍然有效的旧信息，按指令做增/删/改；"
    "**务必保留具体细节：产品名称、基金代码(如 005273)、金额、买入日期等，原样记下，"
    "绝不要概括成'某基金''一些理财'这类模糊说法**。"
    "不要编造未提及的内容，不要加任何解释或客套，只输出资料本身（简洁自然语言、一条一行）。"
)


async def _merge_finance(current: str, instruction: str) -> str:
    """把一条更新指令合并进已存资料，返回更新后的完整资料文本。"""
    llm = get_utility_model()
    prompt = [
        SystemMessage(content=_MERGE_PROMPT),
        HumanMessage(content=f"已保存资料：{current or '（暂无）'}\n更新指令：{instruction}"),
    ]
    msg = await ainvoke_llm(llm, prompt)
    return (getattr(msg, "content", "") or "").strip()


async def profile_node(state: AgentState) -> dict:
    username = state.get("username") or "anonymous"
    current = (state.get("user_finance") or "").strip()
    instruction = last_human(state)

    # 智能整理需调大模型；一旦模型不可达（APIConnectionError 等），绝不能让记录整条失败——
    # 退化为"按原文直接追加保存"，保证数据永不因模型掉线而丢（补代码不依赖大模型，照常做）。
    llm_ok = True
    try:
        merged = (await _merge_finance(current, instruction))[:5000]
    except Exception:  # noqa: BLE001
        llm_ok = False
        merged = (f"{current}\n{instruction}".strip() if current else instruction)[:5000]

    # 记录时自动把"有名字没代码"的基金解析出 6 位代码补进去（唯一匹配才补，绝不瞎猜）
    enriched = enrich_codes_in_text(merged) if merged else merged
    added_codes = enriched != merged
    if enriched:
        await repo.set_user_finance(username, enriched[:5000])

    code_note = "\n（我已自动补上能唯一确定的基金代码，请核对；补不了的说明重名或没查到，可手动给代码。）" if added_codes else ""
    degraded_note = (
        "\n（注：智能整理暂时不可用，已按你说的原样追加保存；稍后可再说「更新我的理财数据：…」让我帮你归整。）"
        if not llm_ok else ""
    )
    reply = (
        "✅ 已记录到你的「我的理财数据」：\n\n"
        f"{enriched or '（没解析出可保存的内容，换个说法再试试，例如「记一下我有10万定期、每月可投3000」）'}"
        f"{code_note}{degraded_note}\n\n"
        "以后给你做推荐/查持仓收益时我会参考它。也可以随时说「更新我的理财数据：…」继续改。"
    )
    return {
        "messages": [AIMessage(content=reply)],
        "agent_type": "profile_agent",
        "user_finance": enriched or current,
    }
