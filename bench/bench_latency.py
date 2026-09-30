"""端到端压测：首字延迟（TTFT）/ 总耗时 / 单轮 Token，按 Agent 分组报 p50 / p95。

对着**真实运行的服务**发 SSE 请求（和前端同一条链路），在客户端计时：
    TTFT  = 发出请求 → 收到第一个非空 token 事件
    Total = 发出请求 → 收到 done 事件
    Tokens= 服务端 meta 事件里的 tokens_used（图内所有 LLM 调用之和，不含后台被动捕捉）

启动服务时务必关掉限流，否则会被每分钟 15 次卡住：
    RATE_LIMIT_PER_MIN=0 RATE_LIMIT_PER_DAY=0 uvicorn app.main:app

用法：
    python -m bench.bench_latency stream --runs 5 --out bench/results/after.csv
    python -m bench.bench_latency cache  --runs 5     # 精确缓存命中 vs 未命中（走 /invoke，需 REDIS_URL）
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")

# 每类各几条代表性问题；首字延迟主要受"LLM 前要做多少事"影响，所以按 Agent 分组看
SCENARIOS: dict[str, list[str]] = {
    "education": ["什么是ETF？", "定投是什么意思", "债券基金为什么也会亏钱"],
    "product": ["有哪些R2以内的稳健产品", "近一年收益率最高的债券基金有哪些"],
    "recommend": ["我有10万闲钱，帮我配置一下", "30岁上班族怎么做理财规划"],
    "research": ["最近有什么理财相关的新闻", "央行最新降息政策是什么"],
    "chat": ["你好", "你能做什么"],
}


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


async def one_stream(client: httpx.AsyncClient, base: str, query: str, headers: dict) -> dict:
    body = {"session_id": f"bench-{uuid.uuid4().hex[:12]}", "query": query}   # 新会话，排除历史长度干扰
    t0 = time.perf_counter()
    ttft = None
    meta: dict = {}
    error = ""
    event = None
    async with client.stream("POST", f"{base}/api/chat/stream", json=body, headers=headers) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if event == "token" and ttft is None and json.loads(data):
                    ttft = time.perf_counter() - t0
                elif event == "meta":
                    meta = json.loads(data)
                elif event == "error":
                    error = json.loads(data).get("message", "error")
                elif event == "done":
                    break
    return {
        "query": query,
        "ttft": ttft,
        "total": time.perf_counter() - t0,
        "agent_type": meta.get("agent_type", ""),
        "tokens": meta.get("tokens_used", 0),
        "answer_chars": len(meta.get("answer", "")),
        "error": error,
    }


async def run_stream(args, headers: dict) -> None:
    rows = []
    async with httpx.AsyncClient(timeout=180) as client:
        # 预热一次：排除进程冷启动、连接建立、首次导入等一次性开销
        await one_stream(client, args.base, "你好", headers)
        for i in range(args.runs):
            for scene, qs in SCENARIOS.items():
                for q in qs:
                    r = await one_stream(client, args.base, q, headers)
                    r["scene"] = scene
                    rows.append(r)
                    flag = f" ERROR={r['error']}" if r["error"] else ""
                    print(f"  [{i + 1}/{args.runs}] {scene:<10} ttft={r['ttft'] or -1:5.2f}s "
                          f"total={r['total']:5.2f}s tok={r['tokens']:>5} {q}{flag}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n明细已写入 {args.out}")

    ok = [r for r in rows if not r["error"] and r["ttft"] is not None]
    print(f"\n== 端到端结果（成功 {len(ok)}/{len(rows)}）==")
    print(f"{'场景':<11}{'TTFT p50':>10}{'TTFT p95':>10}{'总耗时p50':>11}{'Token均值':>11}{'Token p95':>11}")
    groups = defaultdict(list)
    for r in ok:
        groups[r["scene"]].append(r)
    groups["ALL"] = ok
    for scene, rs in groups.items():
        tt = [r["ttft"] for r in rs]
        tot = [r["total"] for r in rs]
        tk = [r["tokens"] for r in rs]
        print(f"{scene:<11}{statistics.median(tt):>9.2f}s{pct(tt, .95):>9.2f}s"
              f"{statistics.median(tot):>10.2f}s{statistics.mean(tk):>11.0f}{pct(tk, .95):>11.0f}")


async def run_cache(args, headers: dict) -> None:
    """同一教育类问题：第一次未命中（跑图 + 写缓存），第二次命中。只有 /invoke 会读缓存。"""
    miss, hit, hit_flags = [], [], []
    async with httpx.AsyncClient(timeout=180) as client:
        for i in range(args.runs):
            q = f"什么是ETF？（缓存测试 {uuid.uuid4().hex[:6]}）"   # 每轮换一个新问题，保证第一次一定未命中
            for bucket in (miss, hit):
                t = time.perf_counter()
                r = await client.post(f"{args.base}/api/chat/invoke", headers=headers,
                                      json={"session_id": f"bench-{uuid.uuid4().hex[:12]}", "query": q})
                r.raise_for_status()
                bucket.append((time.perf_counter() - t, r.json()))
            hit_flags.append(hit[-1][1].get("cached", False))
            print(f"  [{i + 1}/{args.runs}] miss={miss[-1][0]:.2f}s hit={hit[-1][0]:.3f}s cached={hit_flags[-1]}")
    if not any(hit_flags):
        print("\n第二次请求没有命中缓存：检查 REDIS_URL 是否配置、服务是否连上 Redis。")
        return
    m = statistics.median(x[0] for x in miss)
    h = statistics.median(x[0] for x in hit)
    mt = statistics.mean(x[1].get("tokens_used", 0) for x in miss)
    print(f"\n未命中 p50={m:.2f}s（平均 {mt:.0f} tokens）→ 命中 p50={h:.3f}s（0 tokens），延迟降低 {(1 - h / m):.0%}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["stream", "cache"])
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--token", default="", help="AUTH_ENABLED=true 时传 Bearer token")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    await (run_stream if args.mode == "stream" else run_cache)(args, headers)


if __name__ == "__main__":
    asyncio.run(main())
