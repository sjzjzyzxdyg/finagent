"""可靠性工具：限流、熔断、重试。

对标 Java 的 Resilience4j：
- aiolimiter     -> 令牌桶异步限流
- circuitbreaker -> 熔断（连续失败后快速失败，避免雪崩）
- tenacity       -> 指数退避重试
"""
from __future__ import annotations

import asyncio

from aiolimiter import AsyncLimiter
from circuitbreaker import circuit
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.language_models.chat_models import BaseChatModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

# 令牌桶按事件循环缓存，避免跨 loop 复用（AsyncLimiter 要求每个 loop 独立实例）
_limiters: dict[int, AsyncLimiter] = {}


def _get_limiter() -> AsyncLimiter:
    loop_id = id(asyncio.get_running_loop())
    limiter = _limiters.get(loop_id)
    if limiter is None:
        limiter = AsyncLimiter(max_rate=settings.llm_rate_per_sec, time_period=1)
        _limiters[loop_id] = limiter
    return limiter


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=6),
    reraise=True,
)
@circuit(failure_threshold=5, recovery_timeout=30)
async def _guarded_ainvoke(model: BaseChatModel, messages: list[BaseMessage]) -> AIMessage:
    """熔断 + 重试保护下的一次 LLM 调用。"""
    return await model.ainvoke(messages)


async def ainvoke_llm(model: BaseChatModel, messages: list[BaseMessage]) -> AIMessage:
    """带限流 / 熔断 / 重试的 LLM 调用。

    返回完整 AIMessage（保留 usage_metadata，便于 Token 审计）。
    """
    async with _get_limiter():
        reply = await _guarded_ainvoke(model, messages)
    return reply if isinstance(reply, AIMessage) else AIMessage(content=str(reply))


def message_tokens(msg: AIMessage) -> int:
    """从 AIMessage 提取总 Token 数（真实模型才有；mock 返回 0）。"""
    usage = getattr(msg, "usage_metadata", None)
    if usage:
        return int(usage.get("total_tokens", 0))
    meta = getattr(msg, "response_metadata", {}) or {}
    return int((meta.get("token_usage") or {}).get("total_tokens", 0))
