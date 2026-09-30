"""LangGraph checkpointer（会话记忆）。

- 配置 REDIS_URL 时使用 AsyncRedisSaver：对话状态持久化到 Redis，跨进程重启不丢。
- 缺省用 MemorySaver（进程内），零配置可用。

Redis saver 需要异步 asetup() 建索引，故在应用启动（lifespan）时调用 init_checkpointer()。
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.config import settings

_checkpointer = None


async def init_checkpointer():
    """应用启动时调用：初始化 checkpointer（Redis 需要 asetup 建索引）。"""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    if settings.use_redis:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        saver = AsyncRedisSaver(settings.redis_url)
        await saver.asetup()
        _checkpointer = saver
    else:
        _checkpointer = MemorySaver()
    return _checkpointer


def get_checkpointer():
    """返回当前 checkpointer；未初始化时回退内存实现（供测试/离线使用）。"""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer
