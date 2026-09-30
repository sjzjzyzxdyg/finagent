"""本机验证 Redis 记忆（checkpointer）+ 语义缓存。

前置：docker compose up -d 已起 redis-stack-server（含 RediSearch），.env 配置：
    REDIS_URL=redis://localhost:6379/0

用法：
    python -m scripts.check_redis
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from app.config import settings
from app.core.cache import get_cache
from app.memory.checkpointer import init_checkpointer


async def main() -> None:
    if not settings.use_redis:
        print("❌ 未配置 REDIS_URL。请在 .env 填入后重试。")
        return
    print(f"REDIS_URL：{settings.redis_url}")

    # 1) Redis checkpointer（依赖 RediSearch，验证 asetup 成功）
    cp = await init_checkpointer()
    print(f"checkpointer 类型：{type(cp).__name__}")

    from app.agents.graph import build_graph

    graph = build_graph()
    cfg = {"configurable": {"thread_id": "redis-mem-test"}}
    await graph.ainvoke({"messages": [HumanMessage(content="什么是基金")]}, config=cfg)
    await graph.ainvoke({"messages": [HumanMessage(content="那它和股票有什么区别")]}, config=cfg)
    state = await graph.aget_state(cfg)
    n = len(state.values.get("messages", []))
    print(f"同一会话累计消息数：{n}（>=4 说明多轮记忆已持久化到 Redis）")

    # 2) 语义缓存（仅教育类答案会被缓存）
    cache = get_cache()
    await cache.aset("什么是定投", {"answer": "定投是每隔固定时间投入固定金额……", "agent_type": "education_agent", "sources": []})
    hit = await cache.aget("定投到底是什么意思呢")  # 相近问法
    print(f"语义缓存命中：{bool(hit)}" + (f"（相似度 {hit['_score']}）" if hit else ""))

    print("\n✅ Redis 记忆 + 语义缓存验证完成。")


if __name__ == "__main__":
    asyncio.run(main())
