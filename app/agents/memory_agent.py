"""长对话记忆（滚动摘要）。

放在 router 之前：当对话变长时，把"最近若干条之前"的历史压成一段简短摘要存进 state，
各 Agent 通过 history_for_prompt 读取摘要 + 最近消息，从而在长对话里也不丢早前上下文。

为控成本，只有对话足够长、且距上次摘要又新增了若干条时才重算摘要（用非流式工具模型）。
mock 模式或对话尚短时直接跳过。
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import AgentState
from app.config import settings
from app.core.reliability import ainvoke_llm
from app.llm.provider import get_utility_model

_KEEP_RECENT = 6      # 最近保留、不压缩的条数
_REFRESH_EVERY = 6    # 距上次摘要又新增这么多条才重算
_TRIGGER_AT = 12      # 消息条数超过此值才开始做摘要

_PROMPT = (
    "把下面这段个人理财对话压成简短的要点摘要，200 字以内：保留用户的情况/目标/已确认的关键结论/"
    "已问过的重点，丢弃寒暄与冗余。若已有旧摘要，把新对话增量并进去。只输出摘要本身。"
)


async def memory_node(state: AgentState) -> dict:
    msgs = state["messages"]
    n = len(msgs)
    if settings.llm_mock_mode or n < _TRIGGER_AT:
        return {}
    last_at = state.get("summary_at") or 0
    if state.get("summary") and (n - last_at) < _REFRESH_EVERY:
        return {}   # 还没到刷新点，沿用旧摘要

    old = msgs[:-_KEEP_RECENT]   # 压缩"最近 _KEEP_RECENT 条之前"的历史
    convo = "\n".join(
        f"{getattr(m, 'type', '?')}: {getattr(m, 'content', '')}" for m in old
    )[:4000]
    prev = (state.get("summary") or "").strip()
    try:
        msg = await ainvoke_llm(
            get_utility_model(),
            [
                SystemMessage(content=_PROMPT),
                HumanMessage(content=f"已有摘要：{prev or '（无）'}\n\n新的对话：\n{convo}"),
            ],
        )
        summary = (getattr(msg, "content", "") or "").strip()
    except Exception:  # noqa: BLE001 —— 摘要失败不影响对话
        return {}
    if not summary:
        return {}
    return {"summary": summary[:1200], "summary_at": n}
