"""个人理财资料接口（私有，按登录用户隔离）。

用户在此记录自己的持仓 / 资产 / 目标 / 月投入等；推荐 Agent 会读取它做个性化建议。
未开启鉴权(auth_enabled=False)时，get_current_user 返回 'anonymous'，即演示环境下的单一档案。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.security import get_current_user
from app.db import repository as repo

router = APIRouter(
    prefix="/api/profile", tags=["profile"], dependencies=[Depends(get_current_user)]
)


class FinanceIn(BaseModel):
    content: str = ""


@router.get("/finance")
async def get_finance(user: str = Depends(get_current_user)):
    """读取当前用户的理财资料。"""
    return {"content": await repo.get_user_finance(user) or ""}


@router.post("/finance")
async def save_finance(body: FinanceIn, user: str = Depends(get_current_user)):
    """保存当前用户的理财资料（最多 5000 字）。"""
    await repo.set_user_finance(user, (body.content or "")[:5000])
    return {"ok": True}
