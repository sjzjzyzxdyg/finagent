"""推荐链路「出字前等待」的组件级 A/B：隔离出 预热缓存 与 并行取数 各自的贡献。

recommend_node 在调用 LLM 之前要做两件网络慢活：
    A = fetch_live_products（akshare 全市场排行）   B = web_search_context（联网搜索）
优化前 ≈ 冷缓存 + 串行：T = A_cold + B
优化后 ≈ 热缓存 + 并行：T = max(A_warm, B)

本脚本对四种组合各跑 N 次，报 p50 / p95，直接得出"优化前 → 优化后"的数字。
需要 ENABLE_LIVE_DATA=true（akshare 可联网）；B 需要 WEB_SEARCH_API_KEY，没配时 B≈0，只体现预热效果。

用法：
    python -m bench.bench_components --runs 10
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from app.config import settings
from app.tools import live_data
from app.tools.live_data import fetch_live_products
from app.tools.web_tools import web_search_context

sys.stdout.reconfigure(encoding="utf-8")

MAX_RISK = 2          # 未测评用户的默认等级，对应"债券型"
LIMIT = 24            # 与 recommend_node 一致
QUERY = "帮我推荐一个稳健的配置方案"


def _fetch():
    return fetch_live_products("", MAX_RISK, LIMIT)


def _web():
    return web_search_context(QUERY)


async def serial() -> float:
    t = time.perf_counter()
    await asyncio.to_thread(_fetch)
    await asyncio.to_thread(_web)
    return time.perf_counter() - t


async def parallel() -> float:
    t = time.perf_counter()
    await asyncio.gather(asyncio.to_thread(_fetch), asyncio.to_thread(_web))
    return time.perf_counter() - t


async def timed(fn) -> float:
    t = time.perf_counter()
    await asyncio.to_thread(fn)
    return time.perf_counter() - t


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def report(name: str, xs: list[float]) -> None:
    print(f"  {name:<28} p50={statistics.median(xs):6.2f}s  p95={pct(xs, 0.95):6.2f}s  n={len(xs)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()
    if not settings.enable_live_data:
        sys.exit("请在 .env 设置 ENABLE_LIVE_DATA=true 再跑")
    if not settings.web_search_api_key:
        print("[提示] 未配置 WEB_SEARCH_API_KEY：联网搜索耗时≈0，结果只反映预热缓存的效果\n")

    res: dict[str, list[float]] = {k: [] for k in
                                   ("akshare 冷", "akshare 热", "联网搜索",
                                    "冷缓存+串行（优化前）", "冷缓存+并行", "热缓存+串行",
                                    "热缓存+并行（优化后）")}
    for i in range(args.runs):
        # 冷：清空进程内缓存，模拟"用户来问才现拉"
        live_data._cache.clear()
        res["akshare 冷"].append(await timed(_fetch))
        res["akshare 热"].append(await timed(_fetch))
        res["联网搜索"].append(await timed(_web))

        live_data._cache.clear()
        res["冷缓存+串行（优化前）"].append(await serial())
        live_data._cache.clear()
        res["冷缓存+并行"].append(await parallel())
        res["热缓存+串行"].append(await serial())        # 上一步已把缓存拉热
        res["热缓存+并行（优化后）"].append(await parallel())
        print(f"  第 {i + 1}/{args.runs} 轮完成")

    print("\n== 推荐链路：LLM 调用前的数据准备耗时 ==")
    for k, xs in res.items():
        report(k, xs)
    before = statistics.median(res["冷缓存+串行（优化前）"])
    after = statistics.median(res["热缓存+并行（优化后）"])
    if before > 0:
        print(f"\n优化前 → 优化后（p50）：{before:.2f}s → {after:.2f}s，降低 {(1 - after / before):.0%}")


if __name__ == "__main__":
    asyncio.run(main())
