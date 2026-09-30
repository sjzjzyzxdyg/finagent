"""本机验证业务库真实持久化（会话 / 消息 / 知识文档元数据）。

前置：docker compose up -d 已起 MySQL，且 .env 配置：
    MYSQL_DSN=mysql+aiomysql://root:pyfin@localhost:3306/pyfinagent

用法：
    python -m scripts.check_db
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.config import settings
from app.db import repository as repo
from app.db.session import AsyncSessionLocal, engine, init_db


async def main() -> None:
    print(f"业务库 DSN：{settings.db_url}")
    await init_db()

    sid = "check-db-session"
    await repo.ensure_session(sid, user_id=1, title="持久化验证会话")
    await repo.save_message(sid, "user", "什么是ETF？")
    await repo.save_message(
        sid, "assistant", "ETF 是像股票一样能在交易所买卖的指数基金……",
        agent_type="education_agent", tokens_used=123,
    )
    did = await repo.create_knowledge_doc("什么是ETF", category="基金入门", source="demo")
    await repo.update_knowledge_status(did, 1)

    hist = await repo.get_history(sid)
    print(f"\n会话 {sid} 历史 {len(hist)} 条：")
    for m in hist:
        print(f"  #{m.id} {m.role:9} agent={m.agent_type} tok={m.tokens_used} | {m.content[:24]}")

    async with AsyncSessionLocal() as db:
        for t in ("chat_session", "chat_message", "knowledge_doc"):
            n = (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
            print(f"表 {t}: {n} 行")

    await engine.dispose()
    print("\n✅ 业务库持久化验证完成。")


if __name__ == "__main__":
    asyncio.run(main())
