"""本机验证 DeepSeek LLM 接入是否成功。

用法（在项目根目录、已配置 .env 后）：
    python -m scripts.check_llm

会打印：当前模式、一次非流式回复、一次流式 token。
若 .env 未配置 OPENAI_API_KEY，则走 mock 模式（仍可跑通，但非真实模型）。
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.llm.provider import get_chat_model


async def main() -> None:
    mode = "mock（未配置 key）" if settings.llm_mock_mode else f"live -> {settings.llm_model}"
    print(f"当前 LLM 模式：{mode}")
    print(f"Base URL：{settings.openai_base_url}\n")

    llm = get_chat_model()
    prompt = [
        SystemMessage(content="你是简洁的期货助手。"),
        HumanMessage(content="用一句话说明什么是期货基差。"),
    ]

    print("== 非流式 ainvoke ==")
    reply = await llm.ainvoke(prompt)
    print(reply.content)
    usage = getattr(reply, "usage_metadata", None) or getattr(
        reply, "response_metadata", {}
    )
    if usage:
        print(f"[usage] {usage}")

    print("\n== 流式 astream ==")
    async for chunk in llm.astream(prompt):
        print(chunk.content, end="", flush=True)
    print("\n\n✅ LLM 接入验证完成。")


if __name__ == "__main__":
    asyncio.run(main())
