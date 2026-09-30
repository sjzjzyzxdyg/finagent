"""业务库读写（用户 / 会话 / 对话消息 / 个人资料 / 反馈）。

所有函数基于异步 SQLAlchemy Session。配置 MYSQL_DSN 后落到 MySQL；
缺省落到 SQLite（零配置）。上层无需感知底层数据库。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.models import (
    ChatMessage,
    ChatSession,
    Feedback,
    User,
    UserFinance,
    UserRisk,
)
from app.db.session import AsyncSessionLocal


async def get_user_finance(username: str) -> str | None:
    """读取用户私有理财资料。"""
    async with AsyncSessionLocal() as db:
        row = await db.get(UserFinance, username)
        return row.content if row else None


async def set_user_finance(username: str, content: str) -> None:
    """保存/更新用户私有理财资料（upsert）。"""
    async with AsyncSessionLocal() as db:
        row = await db.get(UserFinance, username)
        if row is None:
            db.add(UserFinance(username=username, content=content))
        else:
            row.content = content
        await db.commit()


async def get_user_risk(username: str) -> str | None:
    """读取用户风险画像（测评结果）；未测评返回 None。"""
    async with AsyncSessionLocal() as db:
        row = await db.get(UserRisk, username)
        return row.profile if row else None


async def set_user_risk(username: str, profile: str) -> None:
    """保存/更新用户风险画像（upsert，按用户名持久化，跨会话可用）。"""
    async with AsyncSessionLocal() as db:
        row = await db.get(UserRisk, username)
        if row is None:
            db.add(UserRisk(username=username, profile=profile))
        else:
            row.profile = profile
        await db.commit()


async def get_user(username: str) -> User | None:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(User).where(User.username == username))
        return rows.scalar_one_or_none()


async def create_user(username: str, password_hash: str) -> int:
    """创建用户，返回用户 ID。用户名已存在时由上层先行校验。"""
    async with AsyncSessionLocal() as db:
        u = User(username=username[:64], password_hash=password_hash)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def ensure_session(
    session_id: str,
    user_id: int = 0,
    title: str = "新会话",
    owner: str | None = None,
) -> None:
    """会话不存在则创建；已存在则刷新活跃时间（用于历史列表按最近排序）。

    owner=登录用户名（auth 关闭时为 'anonymous'），用于按用户隔离历史。
    """
    async with AsyncSessionLocal() as db:
        row = await db.get(ChatSession, session_id)
        if row is None:
            db.add(
                ChatSession(
                    id=session_id, user_id=user_id, owner=owner, title=title[:255]
                )
            )
        else:
            row.updated_at = datetime.utcnow()   # 触碰：置顶到历史列表最前
            if owner and not row.owner:           # 旧会话补记归属
                row.owner = owner
        await db.commit()


async def list_sessions(owner: str, limit: int = 50) -> list[ChatSession]:
    """列出某用户的历史会话，最近活跃在前。"""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(ChatSession)
            .where(ChatSession.owner == owner)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        return list(rows.scalars().all())


async def get_chat_session(session_id: str) -> ChatSession | None:
    """读取单个会话（用于历史接口的归属校验）。"""
    async with AsyncSessionLocal() as db:
        return await db.get(ChatSession, session_id)


async def save_message(
    session_id: str,
    role: str,
    content: str,
    agent_type: str | None = None,
    tool_calls: dict | None = None,
    tokens_used: int = 0,
) -> int:
    """写入一条对话消息，返回消息 ID。"""
    async with AsyncSessionLocal() as db:
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            agent_type=agent_type,
            tool_calls=tool_calls,
            tokens_used=tokens_used,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg.id


async def get_history(session_id: str, limit: int = 50) -> list[ChatMessage]:
    """按时间正序读取会话历史。"""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
            .limit(limit)
        )
        return list(rows.scalars().all())


async def save_feedback(
    session_id: str,
    question: str,
    answer: str,
    rating: str,
    agent_type: str | None = None,
    comment: str | None = None,
    applied: int = 0,
) -> int:
    """记录一条用户反馈（点赞/点踩，及可选补充），返回反馈 ID。

    applied 编码审核状态：0=待审核 / 1=已采纳入库 / 2=已拒绝。
    """
    async with AsyncSessionLocal() as db:
        fb = Feedback(
            session_id=session_id,
            question=question,
            answer=answer,
            rating=rating,
            agent_type=agent_type,
            comment=comment,
            applied=applied,
        )
        db.add(fb)
        await db.commit()
        await db.refresh(fb)
        return fb.id


async def list_feedback(
    applied: int | None = None,
    rating: str | None = None,
    limit: int = 100,
) -> list[Feedback]:
    """按审核状态 / 点赞点踩筛选反馈，最新在前。"""
    async with AsyncSessionLocal() as db:
        stmt = select(Feedback)
        if applied is not None:
            stmt = stmt.where(Feedback.applied == applied)
        if rating is not None:
            stmt = stmt.where(Feedback.rating == rating)
        stmt = stmt.order_by(Feedback.id.desc()).limit(limit)
        rows = await db.execute(stmt)
        return list(rows.scalars().all())


async def get_feedback(feedback_id: int) -> Feedback | None:
    async with AsyncSessionLocal() as db:
        return await db.get(Feedback, feedback_id)


async def update_feedback_applied(feedback_id: int, applied: int) -> None:
    """更新反馈的审核状态：0待审核 / 1已入库 / 2已拒绝。"""
    async with AsyncSessionLocal() as db:
        fb = await db.get(Feedback, feedback_id)
        if fb is not None:
            fb.applied = applied
            await db.commit()
