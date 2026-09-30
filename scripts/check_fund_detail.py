"""单只基金详情自测脚本（在本机运行，验证 query_fund_detail 及其依赖的 akshare 接口）。

用法（在项目根目录）：
    python scripts/check_fund_detail.py            # 默认测 019112
    python scripts/check_fund_detail.py 012222     # 指定基金代码

会做两件事：
1. 直接打印三个 akshare 单只基金接口的【原始列名 + 前几行】——若某块在正式功能里没显示，
   多半是列名和代码里的映射对不上；把这段输出发给我，我照着调映射即可。
2. 打印 query_fund_detail 最终拼出的文本（就是 Agent 实际会拿到的内容）。
"""
from __future__ import annotations

import os
import sys

# 把项目根加入模块搜索路径，这样无论从哪运行都能 import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dump(title: str, fn):
    """调用一个返回 DataFrame 的 akshare 接口，打印列名和前几行；失败打印异常。"""
    print("\n" + "=" * 60)
    print(f"【{title}】")
    try:
        df = fn()
    except Exception as e:  # noqa: BLE001
        print(f"  调用失败：{type(e).__name__}: {e}")
        return
    if df is None or getattr(df, "empty", True):
        print("  返回空。")
        return
    print("  列名：", list(df.columns))
    print("  前几行：")
    print(df.head(8).to_string(index=False))


def main() -> None:
    code = (sys.argv[1] if len(sys.argv) > 1 else "019112").strip()
    print(f"测试基金代码：{code}")

    try:
        import akshare as ak
        print("akshare 版本：", ak.__version__)
    except Exception as e:  # noqa: BLE001
        print(f"akshare 不可用：{e}\n请先在本机安装：pip install akshare")
        return

    # 1) 分段回报率（阶段业绩）——雪球
    _dump("① 分段回报率 fund_individual_achievement_xq",
          lambda: ak.fund_individual_achievement_xq(symbol=code))
    # 2) 单位净值走势——天天基金
    _dump("② 单位净值走势 fund_open_fund_info_em(单位净值走势)",
          lambda: ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势"))
    # 注：雪球的基金概况/费率接口(fund_individual_basic_info_xq / _detail_info_xq)在部分
    # akshare 版本会 KeyError:'data'，功能里已不接入；规模/成立日/费率由 Agent 联网查官网。

    from app.config import settings
    settings.enable_live_data = True   # 确保不因未开实时数据而跳过

    # 名称 → 代码 解析自测（记录持仓时自动补代码用的）
    print("\n" + "=" * 60)
    print("【名称→代码 解析 resolve_fund_code / enrich_codes_in_text】")
    from app.tools.live_data import enrich_codes_in_text, resolve_fund_code
    for nm in ["恒越短债债券D", "招商产业债券A", "平安惠泰纯债债券C", "国泰信瑞纯债债券"]:
        print(f"  {nm} -> {resolve_fund_code(nm)}")
    demo = "恒越短债债券D20000元，招商产业债券A10000元，平安惠泰纯债债券C一万，国泰信瑞纯债债券10000元"
    print("  整段补代码：", enrich_codes_in_text(demo))

    # 最终：功能实际拼出的详情文本
    print("\n" + "=" * 60)
    print("【query_fund_detail 实际输出（Agent 会拿到的内容）】")
    from app.tools.live_data import query_fund_detail
    print(query_fund_detail.invoke({"code": code}))


if __name__ == "__main__":
    main()
