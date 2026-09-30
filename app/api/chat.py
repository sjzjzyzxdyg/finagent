"""对话接口：SSE 流式 + 普通同步两种。均落库（会话 + 消息 + Token 审计）。"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import get_graph
from app.agents.router import classify_intent
from app.config import settings
from app.core.cache import get_cache
from app.core.ratelimit import check_and_consume
from app.core.reliability import ainvoke_llm, message_tokens
from app.core.security import get_current_user
from app.db import repository as repo
from app.llm.provider import get_utility_model
from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)])

# 只对"教育知识"类回答做语义缓存：这类答案稳定、可复用；
# 产品/推荐/测评类答案有时效性或因人而异，不缓存以免返回过期或不匹配的结果。
_CACHEABLE_AGENTS = {"education_agent"}

# ---------- 被动捕捉：聊天中随口提到的个人财务信息，静默记入其私有资料 ----------
# 只在句子里出现"我+财务动作/持仓"信号词时才触发（省一次 LLM 调用、也避免误抓提问）。
# 尽量放宽以减少漏抓；真正是否含新信息由后面的 LLM 判定（不含则返回 NONE）。
_FINANCE_SIGNALS = (
    "我有", "我买", "买了", "我卖", "卖了", "我持有", "持有", "持仓", "我存", "存了", "存款",
    "我投", "投了", "我配置", "配置了", "我攒", "攒了", "我定投", "定投", "加仓", "减仓", "清仓",
    "我每月", "每月可投", "每月能投", "月投", "我的目标", "目标是", "我打算", "打算买", "我计划",
    "计划买", "准备买", "我想在", "我的收入", "我工资", "我月薪", "月薪", "年薪", "我的资产",
    "我现在有", "现在持", "目前持", "手里有", "手里", "我已经买", "我把", "代码是", "定期",
    # 明确的"记录/保存投资情况"表达（作为路由漏判时的兜底捕捉）
    "投资情况", "目前的投资", "请你记录", "请记录", "记下来", "记录一下",
)
_CAPTURE_PROMPT = (
    "你在维护某用户的个人理财资料。下面给你他已保存的资料，以及他最新说的一句话。"
    "请判断这句话里是否包含关于**他本人财务状况**的新信息（持仓/资产/理财目标/每月可投入/收入等）。"
    "若有，就把新信息合并进去，输出更新后的**完整**资料，保留仍有效的旧信息、按新信息增/改。"
    "**务必保留具体细节：产品名称、基金代码(如 005273)、金额、买入日期等，原样记下，绝不要概括成"
    "'某基金''一些理财'这类模糊说法**；简洁的自然语言即可、一条一行。"
    "若没有新信息（只是提问、闲聊、泛泛而谈），只输出一个词 NONE，不要输出别的。"
)

# 持有后台任务引用，避免被 GC 提前回收
_bg_tasks: set = set()


async def _capture_finance(username: str, message: str) -> None:
    """从用户消息里被动抽取其个人财务信息并静默合并进私有资料（后台任务，失败无声）。"""
    try:
        if settings.llm_mock_mode:          # mock 模式抽取不出有效信息，跳过（也免得写入占位文本）
            return
        if not any(s in message for s in _FINANCE_SIGNALS):
            return
        if classify_intent(message) == "profile":   # 明确的"更新我的资料"指令由 profile_agent 处理，避免重复
            return
        current = (await repo.get_user_finance(username)) or ""
        msg = await ainvoke_llm(
            get_utility_model(),
            [
                SystemMessage(content=_CAPTURE_PROMPT),
                HumanMessage(content=f"已保存资料：{current or '（暂无）'}\n他最新说的一句话：{message}"),
            ],
        )
        merged = (getattr(msg, "content", "") or "").strip()
        if merged and merged.upper() != "NONE" and merged != current:
            await repo.set_user_finance(username, merged[:5000])
    except Exception as e:  # noqa: BLE001 —— 后台尽力而为，绝不影响正常对话；但失败要留痕便于排查
        logger.warning("被动捕捉财务信息失败（user=%s）：%s: %s", username, type(e).__name__, e)


def _spawn_capture(username: str, message: str) -> None:
    """后台起一个捕捉任务，不阻塞对话响应。"""
    task = asyncio.create_task(_capture_finance(username, message))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


async def _run_and_finalize(
    session_id: str,
    query: str,
    user_finance: str | None = None,
    username: str | None = None,
    risk_profile: str | None = None,
) -> ChatResponse:
    """执行图并落库 + 视情况写缓存。"""
    graph = get_graph()
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=query)],
            "user_finance": user_finance,
            "username": username,
            "risk_profile": risk_profile,
        },
        config=_config(session_id),
    )
    last = result["messages"][-1] if result.get("messages") else None
    answer = last.content if last else ""
    tokens = message_tokens(last) if last else 0
    agent_type = result.get("agent_type") or "unknown"
    sources = result.get("sources") or []

    await repo.save_message(
        session_id, "assistant", answer,
        agent_type=agent_type,
        tool_calls={"sources": sources} if sources else None,
        tokens_used=tokens,
    )
    # 仅缓存教育类答案（稳定、可复用）
    if agent_type in _CACHEABLE_AGENTS:
        await get_cache().aset(
            query, {"answer": answer, "agent_type": agent_type, "sources": sources}
        )
    return ChatResponse(
        session_id=session_id, answer=answer, agent_type=agent_type,
        sources=sources, tokens_used=tokens,
    )


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: str = Depends(get_current_user)):
    """SSE 流式对话：逐 token 推送大模型输出，末尾推送 sources/agent 元数据。"""
    ok, why = check_and_consume(user)             # 限流：保护大模型额度
    if not ok:
        async def limited_gen():
            yield {"event": "meta", "data": json.dumps(
                {"agent_type": "system", "answer": why, "rate_limited": True}, ensure_ascii=False)}
            yield {"event": "token", "data": json.dumps(why, ensure_ascii=False)}
            yield {"event": "done", "data": "[DONE]"}
        return EventSourceResponse(limited_gen())

    await repo.ensure_session(req.session_id, req.user_id, title=req.query, owner=user)
    await repo.save_message(req.session_id, "user", req.query)
    _spawn_capture(user, req.query)               # 被动记录：随口提到的财务信息静默入库
    finance = await repo.get_user_finance(user)   # 用户私有理财资料，注入图供推荐用
    risk = await repo.get_user_risk(user)         # 用户风险画像（持久化），跨会话注入

    async def event_generator():
        graph = get_graph()
        agent_type = "unknown"
        sources: list = []
        tokens = 0
        answer_parts: list[str] = []
        final_answer = ""
        inputs = {
            "messages": [HumanMessage(content=req.query)],
            "user_finance": finance,
            "username": user,
            "risk_profile": risk,
        }
        try:
            async for event in graph.astream_events(
                inputs, config=_config(req.session_id), version="v2"
            ):
                kind = event["event"]
                node = (event.get("metadata") or {}).get("langgraph_node")
                if kind == "on_chat_model_stream":
                    if node == "router":   # 路由内的分类 LLM 不流给前端
                        continue
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        answer_parts.append(chunk.content)
                        # JSON 编码：把换行转义成 \n，避免 SSE 多行 data 丢失换行
                        yield {"event": "token", "data": json.dumps(chunk.content, ensure_ascii=False)}
                elif kind == "on_chat_model_end":
                    if node != "router":
                        tokens += message_tokens(event["data"].get("output"))
                elif kind == "on_chain_end":
                    out = event["data"].get("output")
                    if isinstance(out, dict):
                        agent_type = out.get("agent_type") or agent_type
                        if out.get("sources"):
                            sources = out["sources"]
                        msgs_out = out.get("messages")
                        if msgs_out:
                            content = getattr(msgs_out[-1], "content", "")
                            if content:
                                final_answer = content
        except Exception as e:  # noqa: BLE001
            # 熔断打开 / 大模型出错等：优雅地把错误作为事件推给前端，而不是断流
            yield {
                "event": "error",
                "data": json.dumps({"message": f"服务暂时不可用：{type(e).__name__}"}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "[DONE]"}
            return

        streamed = "".join(answer_parts)
        # 最终答案以节点返回的正文为准（干净、权威）；流式仅用于逐 token 实时展示。
        # ReAct 类 Agent 会有多次模型调用，直接拼接流片段可能混入中间推理，故优先用 final_answer。
        answer = final_answer or streamed
        if not streamed and answer:
            yield {"event": "token", "data": json.dumps(answer, ensure_ascii=False)}

        await repo.save_message(
            req.session_id, "assistant", answer,
            agent_type=agent_type,
            tool_calls={"sources": sources} if sources else None,
            tokens_used=tokens,
        )
        if agent_type in _CACHEABLE_AGENTS and answer:
            await get_cache().aset(
                req.query, {"answer": answer, "agent_type": agent_type, "sources": sources}
            )
        yield {
            "event": "meta",
            "data": json.dumps(
                {"agent_type": agent_type, "sources": sources,
                 "tokens_used": tokens, "answer": answer},
                ensure_ascii=False,
            ),
        }
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_generator())


@router.post("/invoke", response_model=ChatResponse)
async def chat_invoke(req: ChatRequest, user: str = Depends(get_current_user)):
    """非流式对话：一次性返回完整结果并落库。"""
    ok, why = check_and_consume(user)   # 限流：保护大模型额度
    if not ok:
        return ChatResponse(session_id=req.session_id, answer=why, agent_type="system")

    await repo.ensure_session(req.session_id, req.user_id, title=req.query, owner=user)
    await repo.save_message(req.session_id, "user", req.query)
    _spawn_capture(user, req.query)   # 被动记录：随口提到的财务信息静默入库

    # 语义缓存：命中直接返回（仅教育类答案会被缓存）
    hit = await get_cache().aget(req.query)
    if hit:
        await repo.save_message(
            req.session_id, "assistant", hit["answer"],
            agent_type=hit.get("agent_type"), tokens_used=0,
        )
        return ChatResponse(
            session_id=req.session_id, answer=hit["answer"],
            agent_type=hit.get("agent_type", "unknown"),
            sources=hit.get("sources", []), tokens_used=0, cached=True,
        )

    return await _run_and_finalize(
        req.session_id, req.query, await repo.get_user_finance(user),
        username=user, risk_profile=await repo.get_user_risk(user),
    )


@router.get("/sessions", tags=["chat"])
async def list_sessions(user: str = Depends(get_current_user)):
    """当前用户的历史会话列表（最近活跃在前），供前端左侧「会话历史」渲染。"""
    rows = await repo.list_sessions(user)
    return [
        {
            "session_id": s.id,
            "title": s.title or "未命名会话",
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in rows
    ]


@router.get("/history/{session_id}", tags=["chat"])
async def chat_history(session_id: str, user: str = Depends(get_current_user)):
    """读取某会话的历史消息。仅本人（或无归属的旧会话）可读。"""
    sess = await repo.get_chat_session(session_id)
    if sess is not None and sess.owner not in (None, user):
        raise HTTPException(status_code=403, detail="无权访问该会话")
    rows = await repo.get_history(session_id)
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "agent_type": m.agent_type,
            "tokens_used": m.tokens_used,
            # 助手消息落库时把 sources 存进了 tool_calls，取出供前端回填溯源
            "sources": (m.tool_calls or {}).get("sources") if m.tool_calls else None,
        }
        for m in rows
    ]
