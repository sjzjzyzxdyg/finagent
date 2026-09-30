"""鉴权接口：注册 / 用户名密码换取 JWT。

- /register：创建用户（密码 bcrypt 哈希存库）。
- /token：登录，仅校验真实用户表（bcrypt 校验）。已去除演示账号兜底，按生产标准执行。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends

from app.core.security import create_access_token, hash_password, verify_password
from app.db import repository as repo

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token(username: str) -> dict:
    return {"access_token": create_access_token(subject=username), "token_type": "bearer"}


@router.post("/register")
async def register(form: OAuth2PasswordRequestForm = Depends()):
    """注册新用户，成功后直接返回登录 token。"""
    username = form.username.strip()
    if len(username) < 2 or len(form.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名至少 2 位、密码至少 6 位",
        )
    if await repo.get_user(username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="用户名已存在"
        )
    await repo.create_user(username, hash_password(form.password))
    return _token(username)


@router.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 密码模式登录：仅校验真实用户表（bcrypt）。"""
    user = await repo.get_user(form.username)
    if user and verify_password(form.password, user.password_hash):
        return _token(user.username)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="用户名或密码错误",
        headers={"WWW-Authenticate": "Bearer"},
    )
