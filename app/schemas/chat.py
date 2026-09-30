"""对话相关的 Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID，用于串联多轮记忆")
    query: str = Field(..., min_length=1, description="用户问题")
    user_id: int = Field(default=0, description="用户 ID（演示默认 0）")


class SourceRef(BaseModel):
    """知识库引用溯源。"""

    title: str
    page: int | None = None
    chunk_index: int | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    agent_type: str
    sources: list[SourceRef] = Field(default_factory=list)
    tokens_used: int = 0
    cached: bool = False       # 是否命中语义缓存
