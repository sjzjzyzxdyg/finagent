"""知识库入库脚本：把内置理财教育内容（或指定目录的文档）写入向量库。

用法：
    python -m scripts.seed_knowledge            # 写入内置理财知识库
    python -m scripts.seed_knowledge data/kb    # 额外导入目录下的 pdf/txt/md
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.data.knowledge import seed_knowledge
from app.rag.chunking import chunk_pages
from app.rag.parser import parse_document
from app.rag.vector_store import get_vector_store


def ingest_dir(dir_path: str) -> int:
    store = get_vector_store()
    total = 0
    for f in Path(dir_path).glob("**/*"):
        if f.suffix.lower() not in (".pdf", ".txt", ".md"):
            continue
        pages = parse_document(f)
        chunks = chunk_pages(pages)
        store.add_many(
            [c.content for c in chunks],
            [{"title": f.stem, "page": c.page, "chunk_index": c.chunk_index} for c in chunks],
        )
        total += len(chunks)
        print(f"  + {f.name}: {len(chunks)} chunks")
    return total


if __name__ == "__main__":
    n = seed_knowledge(force=True)
    print(f"内置知识库写入 {n} 个片段")
    if len(sys.argv) > 1:
        n2 = ingest_dir(sys.argv[1])
        print(f"目录导入 {n2} 个片段")
    print(f"向量库当前大小：{len(get_vector_store())}")
