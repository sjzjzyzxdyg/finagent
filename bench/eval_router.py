"""意图路由准确率评测。

直接调用线上同一个 router_node（不是另写一套逻辑），在标注集上算：
整体 / typical / hard 准确率、各意图 P/R/F1、关键词未命中率（=需要兜底的比例）、错例清单、单条耗时。

用法：
    python -m bench.eval_router                  # 纯关键词路由（默认线上配置，离线可跑，不耗 token）
    python -m bench.eval_router --llm            # 关键词判不出时走 LLM 分类（需 OPENAI_API_KEY）
    python -m bench.eval_router --cases other.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agents.router import has_holding_query, plan_intents, router_node
from app.config import settings

sys.stdout.reconfigure(encoding="utf-8")
DEFAULT_CASES = Path(__file__).with_name("intent_cases.jsonl")


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def predict(q: str) -> tuple[str, float]:
    t = time.perf_counter()
    out = await router_node({"messages": [HumanMessage(content=q)]})
    return "+".join(out.get("intents") or [out.get("intent")]), (time.perf_counter() - t) * 1000


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--llm", action="store_true", help="开启 ROUTER_LLM_FALLBACK")
    args = ap.parse_args()

    settings.router_llm_fallback = args.llm
    if args.llm and settings.llm_mock_mode:
        sys.exit("--llm 需要在 .env 配置 OPENAI_API_KEY")

    cases = load_cases(args.cases)
    rows = []
    for c in cases:
        pred, ms = await predict(c["q"])
        # 关键词层是否判出（判不出 = 进入兜底：LLM 或默认 education）
        kw_hit = has_holding_query(c["q"]) or bool(plan_intents(c["q"]))
        rows.append({**c, "pred": pred, "ms": ms, "kw_hit": kw_hit, "ok": pred == c["label"]})

    def acc(rs):
        return sum(r["ok"] for r in rs) / len(rs) if rs else float("nan")

    mode = "关键词 + LLM 兜底" if args.llm else "纯关键词（兜底=education）"
    print(f"\n== 意图路由评测 | 模式：{mode} | 样本数：{len(rows)} ==")
    print(f"整体准确率 : {acc(rows):.1%}")
    for split in ("typical", "hard"):
        rs = [r for r in rows if r.get("split") == split]
        print(f"  {split:<8}: {acc(rs):.1%}  (n={len(rs)})")
    miss = [r for r in rows if not r["kw_hit"]]
    print(f"关键词未命中率: {len(miss) / len(rows):.1%}（这些样本靠兜底，兜底后准确率 {acc(miss):.1%}）")
    lat = [r["ms"] for r in rows]
    print(f"单条耗时 ms : p50={statistics.median(lat):.2f}  max={max(lat):.2f}")

    print("\n各意图 Precision / Recall / F1：")
    labels = sorted({r["label"] for r in rows} | {r["pred"] for r in rows})
    for lb in labels:
        tp = sum(r["pred"] == lb and r["label"] == lb for r in rows)
        fp = sum(r["pred"] == lb and r["label"] != lb for r in rows)
        fn = sum(r["pred"] != lb and r["label"] == lb for r in rows)
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
        print(f"  {lb:<22} P={p:.2f} R={rc:.2f} F1={f1:.2f}  (support={tp + fn})")

    confusions = Counter((r["label"], r["pred"]) for r in rows if not r["ok"])
    if confusions:
        print("\n主要混淆（真实 → 预测）：")
        for (t, p), n in confusions.most_common():
            print(f"  {t} → {p}: {n}")

    errs = [r for r in rows if not r["ok"]]
    if errs:
        print("\n错例：")
        by_label = defaultdict(list)
        for r in errs:
            by_label[r["label"]].append(r)
        for lb, rs in by_label.items():
            for r in rs:
                print(f"  [{lb} → {r['pred']}] {r['q']}")


if __name__ == "__main__":
    asyncio.run(main())
