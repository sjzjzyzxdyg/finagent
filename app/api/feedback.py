"""用户反馈接口：对回答点赞/点踩（满意度信号，落库留存）。

去除了原先"点踩补充→写入知识库"的闭环（RAG 已下线），现在仅记录点赞/点踩。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.db import repository as repo

router = APIRouter(
    prefix="/api/feedback", tags=["feedback"], dependencies=[Depends(get_current_user)]
)


class FeedbackRequest(BaseModel):
    session_id: str
    rating: str = Field(..., description="up=点赞 / down=点踩")
    question: str = ""
    answer: str = ""
    agent_type: str | None = None
    comment: str | None = ""   # 可选的一句话补充（仅记录，不再入库）


@router.post("")
async def submit_feedback(req: FeedbackRequest):
    """记录一条点赞/点踩满意度反馈。"""
    try:
        await repo.save_feedback(
            session_id=req.session_id, question=req.question, answer=req.answer,
            rating=req.rating, agent_type=req.agent_type,
            comment=(req.comment or "").strip() or None, applied=0,
        )
    except Exception:  # noqa: BLE001 —— 反馈落库失败不应影响用户，尽力而为
        pass
    return {"ok": True, "rating": req.rating}
