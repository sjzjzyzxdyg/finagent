"""FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import auth, chat, feedback, profile
from app.config import settings
from app.db.session import init_db
from app.memory.checkpointer import init_checkpointer

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 运行期严禁 mock：未配置 OPENAI_API_KEY 会让大模型退化成占位回显（mock 仅供测试）。
    # 这里直接拒绝启动，确保"真正运行项目"时绝不会悄悄进 mock 模式、给用户假答案。
    if settings.llm_mock_mode:
        raise RuntimeError(
            "未配置 OPENAI_API_KEY，应用会进入 mock 回显模式；请在 .env 填入真实大模型 key 后再启动。"
            "（mock 仅供离线测试使用，不允许在运行项目时出现。）"
        )
    # 生产安全闸：开启鉴权却仍用弱/占位 JWT 密钥时，拒绝启动，避免 token 可被伪造
    if settings.auth_enabled and settings.insecure_jwt_secret:
        raise RuntimeError(
            "AUTH_ENABLED=true 但 JWT_SECRET 仍是占位/过短值；"
            "请在 .env 设置一个≥16位的强随机 JWT_SECRET 后再启动。"
        )
    # 启动：建表（SQLite 演示）；生产用 Alembic
    try:
        await init_db()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] init_db skipped: {e}")
    # 初始化会话记忆 checkpointer（Redis 需异步建索引）
    try:
        await init_checkpointer()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] init_checkpointer fell back to memory: {e}")
    # 注：教育 Agent 已改为直接依据大模型作答（去除 RAG），不再需要启动时播种知识库/建向量索引。
    # 后台预热 akshare 基金排行：定时把常用类型刷进缓存，用户查询直接命中，避免现拉的慢等待。
    try:
        from app.tools.live_data import start_live_data_refresher

        await start_live_data_refresher()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] live-data refresher not started: {e}")
    yield


app = FastAPI(
    title=settings.app_name,
    description=settings.app_desc,
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(feedback.router)   # 仅保留一键点赞/点踩满意度，不再喂公共知识库
app.include_router(profile.router)


@app.get("/", include_in_schema=False)
async def index():
    """前端聊天页面。禁用缓存，避免浏览器用旧版页面（会导致看不到流式等新改动）。"""
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm_mode": "mock" if settings.llm_mock_mode else "live",
        "redis": settings.use_redis,
        "auth_enabled": settings.auth_enabled,
        "live_data": settings.enable_live_data,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
