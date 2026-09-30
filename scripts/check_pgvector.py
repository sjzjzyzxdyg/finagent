"""本机验证 pgvector 真实向量库：建表 -> 灌样例知识 -> 向量检索。

前置：
  1. 起一个带 pgvector 的 Postgres：
       docker run -d --name pgvector -e POSTGRES_PASSWORD=pyfin \
         -e POSTGRES_DB=pyfinagent -p 5432:5432 pgvector/pgvector:pg16
  2. 在 .env 配置：
       PGVECTOR_DSN=postgresql://postgres:pyfin@localhost:5432/pyfinagent
     （建议同时配置 EMBEDDING_API_KEY 用真实向量；否则回退哈希伪向量也能跑通链路）

用法：
    python -m scripts.check_pgvector
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.rag.vector_store import get_vector_store

SAMPLES = [
    ("什么是ETF", "ETF 是可以像股票一样在交易所买卖的指数基金，费率低、持仓透明。"),
    ("什么是定投", "定投是每隔固定时间投入固定金额，摊薄成本、平滑波动，适合长期坚持。"),
    ("风险与收益", "收益和风险成正比，没有又高又稳又保本的产品，先明确风险承受力再选产品。"),
]


def apply_schema() -> None:
    import psycopg2

    dsn = settings.pgvector_dsn.replace("+asyncpg", "").replace("+psycopg2", "")
    ddl = Path(__file__).resolve().parents[1] / "sql" / "pgvector_schema.sql"
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(ddl.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    print("✅ 建表/扩展完成（幂等）")


def main() -> None:
    if not settings.use_pgvector:
        print("❌ 未配置 PGVECTOR_DSN。请在 .env 填入 Postgres 连接串后重试。")
        return

    print(f"PGVECTOR_DSN 已配置；embedding {'真实云端' if settings.embedding_live else '哈希回退'}")
    apply_schema()

    store = get_vector_store()
    print(f"向量库类型：{type(store).__name__}")

    store.add_many(
        [c for _, c in SAMPLES],
        [{"title": t, "page": 1, "chunk_index": i} for i, (t, _) in enumerate(SAMPLES)],
    )
    print(f"已写入样例，当前库内片段数：{len(store)}")

    q = "定投是什么意思"
    print(f"\n检索：{q}")
    for chunk, score in store.search(q, top_k=3):
        print(f"  [{score:.3f}] {chunk.metadata.get('title')} | {chunk.content[:30]}...")
    print("\n✅ pgvector 向量库验证完成。")


if __name__ == "__main__":
    main()
