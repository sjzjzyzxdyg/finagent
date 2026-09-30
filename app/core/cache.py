"""问答精确缓存（Exact-match Cache）。

把"教育类"问题的答案按规范化后的问题文本存到 Redis；完全相同的问题再次问到时直接返回，
省去一次大模型调用（降本 + 降延迟）。已随 RAG 一起去除向量/语义匹配，改为**精确匹配**——
实现简单、无 embedding 依赖。

- 未配置 REDIS_URL 时自动禁用（所有操作 no-op）。
"""
from __future__ import annotations

import hashlib
import json

from app.config import settings

_PREFIX = "pyfin:qacache:"
_TTL = 7 * 24 * 3600   # 7 天过期，避免答案长期陈旧


def _key(query: str) -> str:
    norm = " ".join((query or "").strip().lower().split())   # 规范化：去多余空白、统一大小写
    return _PREFIX + hashlib.sha1(norm.encode("utf-8")).hexdigest()


class QACache:
    def __init__(self) -> None:
        self._enabled = settings.use_redis
        self._client = None
        if self._enabled:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def aget(self, query: str) -> dict | None:
        """命中返回缓存 payload（含 answer/agent_type/sources），否则 None。"""
        if not self._enabled:
            return None
        try:
            raw = await self._client.get(_key(query))
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001 —— 缓存不可用不应影响主流程
            return None

    async def aset(self, query: str, payload: dict) -> None:
        """写入一条缓存（按问题精确匹配，带 7 天过期）。"""
        if not self._enabled:
            return
        try:
            await self._client.set(
                _key(query), json.dumps(payload, ensure_ascii=False), ex=_TTL
            )
        except Exception:  # noqa: BLE001
            pass


_cache: QACache | None = None


def get_cache() -> QACache:
    global _cache
    if _cache is None:
        _cache = QACache()
    return _cache
