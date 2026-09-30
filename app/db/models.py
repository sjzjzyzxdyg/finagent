"""SQLAlchemy 2.0 声明式 ORM 模型，对应 sql/mysql_schema.sql。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# BIGINT 自增主键在 SQLite 下不是 rowid 别名，需降级为 INTEGER 才能自增；
# MySQL 下仍是 BIGINT AUTO_INCREMENT。
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    """平台用户（注册/登录）。表名用 app_user 避开数据库保留字 user。"""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserFinance(Base):
    """用户私有的理财资料（持仓/目标/月投入等自由文本），按用户名隔离。"""

    __tablename__ = "user_finance"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserRisk(Base):
    """用户风险画像（测评结果，如 '稳健型·R2'），按用户名持久化，跨会话可用。"""

    __tablename__ = "user_risk"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # 归属用户名（按登录用户隔离历史；auth 关闭时为 "anonymous"）。旧库无此列时可空。
    owner: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))  # user / assistant / system / tool
    content: Mapped[str] = mapped_column(Text)
    agent_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Feedback(Base):
    """用户对某条回答的点赞/点踩满意度反馈。"""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    agent_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rating: Mapped[str] = mapped_column(String(8))          # up / down
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied: Mapped[int] = mapped_column(SmallInteger, default=0)  # 1=已用于更新知识库
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
