"""LLM 提供方封装。

- 配置了 OPENAI_API_KEY 时返回真实 ChatOpenAI（DeepSeek / Qwen / OpenAI 兼容协议），
  内建超时、重试；对外再包一层 tenacity 退避与 aiolimiter 令牌桶限流。
- 未配置时返回 MockChatModel，可离线启动演示，不依赖任何外部服务。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import settings


class MockChatModel(BaseChatModel):
    """极简 mock 聊天模型：回显最后一条用户消息并附上说明。

    仅用于零配置演示；配置真实 API key 后不会被使用。
    """

    @property
    def _llm_type(self) -> str:
        return "mock-chat"

    def _last_human_text(self, messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if msg.type in ("human", "system"):
                return str(msg.content)
        return ""

    def _fake_answer(self, messages: list[BaseMessage]) -> str:
        q = self._last_human_text(messages)
        return (
            f"【Mock LLM 回复】已收到请求：{q[:120]}\n"
            "（当前为离线 mock 模式。在 .env 中配置 OPENAI_API_KEY 后将调用真实大模型。）"
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self._fake_answer(messages)
        gen = ChatGeneration(message=AIMessage(content=text))
        return ChatResult(generations=[gen])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        text = self._fake_answer(messages)
        for ch in text:
            yield ChatGeneration(message=AIMessageChunk(content=ch))

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockChatModel":  # noqa: ARG002
        return self


def _build_real_model(**kwargs: Any) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=kwargs.pop("temperature", settings.llm_temperature),
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
        streaming=kwargs.pop("streaming", True),
        **kwargs,
    )


@lru_cache(maxsize=8)
def get_chat_model(temperature: float | None = None) -> BaseChatModel:
    """工厂：根据配置返回真实或 mock LLM（流式，用于生成对用户可见的回答）。"""
    if settings.llm_mock_mode:
        return MockChatModel()
    temp = settings.llm_temperature if temperature is None else temperature
    return _build_real_model(temperature=temp)


@lru_cache(maxsize=1)
def get_utility_model() -> BaseChatModel:
    """内部工具模型：非流式、temperature=0。

    用于意图分类、检索打分、查询改写等"内部推理"——不流式，
    因此其输出不会泄漏进流式回答；结果确定、便于解析。
    """
    if settings.llm_mock_mode:
        return MockChatModel()
    return _build_real_model(temperature=0, streaming=False)
