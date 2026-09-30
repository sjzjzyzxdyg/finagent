"""联网取真实数据层（默认可开关）。

降级链：akshare 公开数据（真实基金）→ 联网搜索（需 key）→ 内置示例库。
akshare 说明：能拿到真实的基金名称/近1年收益/费率，但不提供官方风险等级 R1-R5，
因此这里按【基金类型】映射一个近似风险等级，并把用户的风险约束映射到对应基金类型去拉数据。
真正带官方风险评级的全量数据应对接持牌数据供应商。

所有外部调用都被 try/except 包住并降级，绝不因取数失败让请求报错。
akshare 拉的是全市场排行（较慢）。为避免"用户来问时才现拉"的等待，这里采用
【后台定时预热 + stale-while-revalidate】：后台循环定时把常用基金类型刷进缓存，
用户请求直接命中热缓存；即便偶然撞上过期，也先秒回旧数据、由后台负责刷新。
"""
from __future__ import annotations

import asyncio
import re
import time

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import settings

# 用户可承受的最高风险等级 → akshare 开放式基金排行支持的“基金类型”
_RISK_TO_TYPE = {1: "债券型", 2: "债券型", 3: "混合型", 4: "股票型", 5: "股票型"}
# 基金类型 → 近似风险等级（akshare 无官方 R1-R5，按类型估算）
_TYPE_TO_RISK = {"债券型": 2, "指数型": 3, "混合型": 4, "股票型": 5, "QDII": 5, "FOF": 3, "全部": None}
# 后台预热的常用基金类型（推荐/产品按风险等级都会映射到这几类）
_PREWARM_TYPES = ("债券型", "混合型", "股票型")

_TTL = 600  # 缓存"新鲜"判定秒数：超过即视为陈旧（仍可先返回旧数据，不阻塞用户）
_cache: dict[str, tuple[float, list[dict]]] = {}   # fund_type -> (ts, rows)
_refresher_task: asyncio.Task | None = None        # 后台预热循环任务引用（防被 GC）

_DETAIL_TTL = 1800   # 单只基金详情缓存秒数（净值日更，30 分钟足够）
_detail_cache: dict[str, tuple[float, str]] = {}   # code -> (ts, 详情文本)


def _to_float(x) -> float | None:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(x)))
    except (TypeError, ValueError):
        return None


def _keywords(text: str) -> list[str]:
    stop = {"有哪些", "推荐", "帮我", "查询", "对比", "比较", "一下", "怎么样", "如何", "低风险",
            "稳健", "收益", "费率", "基金", "产品", "以内", "以下", "含", "元"}
    toks = re.findall(r"[一-龥A-Za-z0-9]{2,}", text)
    return [t for t in toks if t not in stop]


# ---------------- 方法1：akshare 公开数据 ----------------

def _do_fetch(fund_type: str) -> list[dict] | None:
    """真正拉取并解析 akshare 排行（慢），成功则写入缓存。与新鲜度无关——供后台强制刷新用。"""
    try:
        import akshare as ak

        df = ak.fund_open_fund_rank_em(symbol=fund_type)
    except Exception:  # noqa: BLE001 —— 未安装/网络/接口变更，降级
        return None
    try:
        cols = set(df.columns)

        def col(*cands):
            for c in cands:
                if c in cols:
                    return c
            return None

        c_code = col("基金代码"); c_name = col("基金简称", "基金名称")
        c_ret = col("近1年", "近1年%"); c_fee = col("手续费")
        if not (c_code and c_name):
            return None
        risk = _TYPE_TO_RISK.get(fund_type)
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "产品代码": str(r[c_code]),
                "产品名称": str(r[c_name]),
                "产品类型": f"公募基金·{fund_type}",
                "风险等级": risk if risk is not None else "按类型估算",
                "近1年年化(%)": _to_float(r[c_ret]) if c_ret else None,
                "综合费率(%)": _to_float(r[c_fee]) if c_fee else None,
                "起投(元)": 10,
                "流动性": "开放式，一般 T+1~T+2 赎回",
                "建议持有": "视基金类型而定",
                "风险说明": "实时数据；风险等级按基金类型估算，实际以销售适当性为准",
            })
        # 按近1年收益从高到低
        rows.sort(key=lambda x: (x["近1年年化(%)"] is not None, x["近1年年化(%)"] or -1e9), reverse=True)
        _cache[fund_type] = (time.time(), rows)
        return rows
    except Exception:  # noqa: BLE001
        return None


def _fetch_rank(fund_type: str) -> list[dict] | None:
    """热路径（用户请求走这里）：新鲜缓存→秒回；陈旧但有旧数据→先返回旧数据（后台会刷新）；
    完全没有缓存（冷启动、后台还没预热到）→只能同步拉一次。返回统一口径行列表；失败返回 None。"""
    now = time.time()
    hit = _cache.get(fund_type)
    if hit and now - hit[0] < _TTL:
        return hit[1]          # 新鲜，直接命中
    if hit:
        return hit[1]          # 陈旧也先秒回（stale-while-revalidate；后台预热循环负责刷新）
    return _do_fetch(fund_type)  # 冷启动兜底：没有任何缓存时同步拉一次


def _akshare_funds(query: str, max_risk: int | None, limit: int) -> list[dict] | None:
    """按风险约束选基金类型拉真实基金；有关键词则按基金名过滤。失败返回 None。"""
    fund_type = _RISK_TO_TYPE.get(max_risk) if max_risk else "混合型"
    rows = _fetch_rank(fund_type)
    if not rows:
        return None
    kws = _keywords(query)
    if kws:
        hit = [r for r in rows if any(k in r["产品名称"] for k in kws)]
        if hit:
            rows = hit
    return rows[:limit] or None


# ---------------- 方法2：联网搜索 ----------------

def _web_search(query: str, limit: int) -> list[dict] | None:
    """通用联网搜索（默认 Tavily 风格）。未配置 key 或失败返回 None。"""
    if not settings.web_search_api_key:
        return None
    try:
        import httpx

        resp = httpx.post(
            settings.web_search_url,
            json={"api_key": settings.web_search_api_key,
                  "query": f"{query} 理财 基金 收益 费率", "max_results": limit, "search_depth": "basic"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or data.get("data") or []
        out = [{
            "产品名称": it.get("title") or it.get("name") or "网络结果",
            "摘要": (it.get("content") or it.get("snippet") or "")[:300],
            "来源链接": it.get("url") or it.get("link") or "",
            "数据来源": "联网搜索",
        } for it in results[:limit]]
        return out or None
    except Exception:  # noqa: BLE001
        return None


# ---------------- 降级编排 ----------------

def fetch_live_products(query: str, max_risk: int | None = None, limit: int = 6) -> tuple[list[dict] | None, str]:
    """联网取真实产品信息。返回 (结果 or None, 数据来源标记: akshare/web/mock)。"""
    if not settings.enable_live_data:
        return None, "mock"
    rows = _akshare_funds(query, max_risk, limit)
    if rows:
        return rows, "akshare"
    rows = _web_search(query, limit)
    if rows:
        return rows, "web"
    return None, "mock"


# ---------------- 候选去同质化（借鉴 MMR：既要收益高、又要多样，别推一堆雷同的） ----------------

# 份额类别尾巴：同一只基金的 A/C/E 等份额，本质是同一产品，推荐里不该重复占位
_CLASS_TAIL = re.compile(r"[（(](前端|后端|美元现汇|美元现钞|人民币|[ABCDE]类)[)）]\s*$|[ABCDEＡＢＣＤＥ]\s*$")


def _base_fund_name(name: str) -> str:
    """去掉份额类别后缀，得到"同一只基金"的基名（华夏鼎利债券C → 华夏鼎利债券）。"""
    n = (name or "").strip()
    prev = None
    while n and n != prev:               # 可能有"…债券A类(后端)"这种叠加尾巴，循环剥
        prev = n
        n = _CLASS_TAIL.sub("", n).strip()
    return n or (name or "").strip()


# 常见公募基金公司品牌名（按其在"基金简称"里出现的写法）；用于把候选按【真实公司】分散。
# akshare 排行行不带公司字段、单只基金的公司接口在部分版本又不可用，故用"品牌名最长前缀匹配"来判公司——
# 确定、离线、可测；匹配不到再退回前两字近似。可随时往这个列表补充公司名。
_FUND_COMPANIES = (
    "华夏", "易方达", "嘉实", "南方", "博时", "广发", "富国", "汇添富", "华安", "工银瑞信", "工银",
    "建信", "中银", "交银施罗德", "交银", "招商", "兴证全球", "兴全", "景顺长城", "银华", "鹏华",
    "国泰", "天弘", "中欧", "华宝", "民生加银", "农银汇理", "农银", "平安", "万家", "长城", "大成",
    "诺安", "国投瑞银", "上投摩根", "摩根", "华泰柏瑞", "浦银安盛", "兴业", "光大保德信", "光大",
    "中信保诚", "中信建投", "信达澳亚", "信达澳银", "泰达宏利", "长信", "长盛", "金鹰", "融通",
    "东证资管", "东方红", "东方", "中加", "永赢", "财通", "国联安", "华富", "前海开源", "新华",
    "华商", "圆信永丰", "中庚", "安信", "睿远", "泓德", "鑫元", "恒越", "中泰", "西部利得",
    "国寿安保", "太平", "红土创新", "方正富邦", "德邦", "创金合信", "诺德", "湘财", "中融", "蜂巢",
    "惠升", "同泰", "中航", "英大", "贝莱德", "富达", "路博迈", "施罗德", "联博", "博道", "凯石",
    "泉果", "汇泉", "苏新", "国融", "先锋", "益民", "华融", "浙商", "国金", "红塔红土", "金信",
)
# 预排序：长名在前，保证"东方红/工银瑞信"这类先于"东方/工银"匹配，避免误归组
_FUND_COMPANIES_SORTED = tuple(sorted(set(_FUND_COMPANIES), key=len, reverse=True))


def _fund_company(name: str) -> str:
    """从基金名判定基金公司：已知品牌名做最长前缀匹配；匹配不到退回前两字近似。"""
    n = (name or "").strip()
    for brand in _FUND_COMPANIES_SORTED:
        if n.startswith(brand):
            return brand
    return n[:2]


def diversify_rows(rows: list[dict], limit: int, per_company: int = 2) -> list[dict]:
    """给推荐候选去同质化：同一只基金的不同份额只留收益最高的一个，再限制每家公司最多 per_company 只，
    让候选覆盖更多不同产品/机构。要求 rows 已按收益从高到低排序（akshare 排行本就是）。"""
    if not rows:
        return rows
    seen_base: set[str] = set()
    company_count: dict[str, int] = {}
    picked: list[dict] = []
    for r in rows:
        name = str(r.get("产品名称", ""))
        base = _base_fund_name(name)
        if base in seen_base:
            continue                      # 同一只基金的其它份额，跳过（已留了收益最高的）
        company = _fund_company(base)     # 按真实基金公司分散（品牌名最长前缀匹配）
        if per_company and company_count.get(company, 0) >= per_company:
            continue
        seen_base.add(base)
        company_count[company] = company_count.get(company, 0) + 1
        picked.append(r)
        if len(picked) >= limit:
            return picked
    # 公司限量把一些"不同基金"挡掉、导致不足时，放宽公司限制补齐——但**绝不重新加回同一只基金的份额**
    if len(picked) < limit:
        chosen_ids = {id(x) for x in picked}
        for r in rows:
            if id(r) in chosen_ids:
                continue
            base = _base_fund_name(str(r.get("产品名称", "")))
            if base in seen_base:          # 同一只基金的其它份额，不补回
                continue
            seen_base.add(base)
            picked.append(r)
            if len(picked) >= limit:
                break
    return picked


# ---------------- 后台预热：定时刷新缓存，让用户查询直接命中热缓存 ----------------

async def refresh_live_data_once() -> None:
    """把常用基金类型的排行强制刷新一遍（启动预热 / 定时刷新调用）。慢活放线程池，互不阻塞。"""
    for fund_type in _PREWARM_TYPES:
        try:
            await asyncio.to_thread(_do_fetch, fund_type)
        except Exception:  # noqa: BLE001 —— 单个类型失败不影响其它
            pass


async def start_live_data_refresher() -> None:
    """启动后台预热循环：先立即预热一次，再按 live_data_refresh_sec 间隔定时刷新。

    在 FastAPI lifespan 里调用；创建后台任务即返回，不阻塞启动。未开启实时数据时直接跳过。
    """
    global _refresher_task
    if not settings.enable_live_data:
        return
    if _refresher_task and not _refresher_task.done():
        return   # 避免重复启动

    async def _loop() -> None:
        await refresh_live_data_once()   # 启动即预热，别等第一个用户来触发
        interval = max(60, settings.live_data_refresh_sec)
        while True:
            await asyncio.sleep(interval)
            await refresh_live_data_once()

    _refresher_task = asyncio.create_task(_loop())


class FundQueryInput(BaseModel):
    query: str = Field("", description="关键词，如基金名/主题，可留空表示不限")
    max_risk_level: int | None = Field(None, ge=1, le=5, description="可接受的最高风险等级 R1-R5")


@tool(args_schema=FundQueryInput)
def query_funds(query: str = "", max_risk_level: int | None = None) -> str:
    """查询理财产品的结构化数据：取 akshare 真实公募基金（名称/近1年收益/费率/风险）。
    取不到时如实返回"查不到"（默认不再用示例数据兜底）。用于拿到产品的具体数字。"""
    rows, source = fetch_live_products(query or "基金", max_risk_level, 8)
    if not rows and settings.sample_fallback:   # 仅当显式开启才回退内置示例库（默认关闭）
        from app.tools.product_tools import search_products

        res = search_products.invoke(
            {"category": None, "max_risk_level": max_risk_level, "keyword": query or None, "limit": 8}
        )
        rows = res.get("products", [])
        source = "内置示例库"
    if not rows:
        return "（暂时未取到符合条件的真实产品数据，请稍后再试或换个关键词；不提供演示数据。）"
    lines = [f"数据来源：{source}（{'实时' if source == 'akshare' else '示例/参考'}）"]
    for r in rows[:8]:
        lines.append(
            f"- {r.get('产品名称', '')}（{r.get('产品代码', '')}）| 风险 {r.get('风险等级', '?')} | "
            f"近1年 {r.get('近1年年化(%)', '?')}% | 费率 {r.get('综合费率(%)', '?')}% | "
            f"起投 {r.get('起投(元)', '?')}元 | {r.get('流动性', '')}"
        )
    return "\n".join(lines)


# ---------------- 单只基金详情（按代码查：分段回报/历史净值/概况/费率） ----------------

def _fetch_fund_detail(code: str) -> str:
    """按基金代码拉单只基金的详细信息（akshare 单只基金接口，较慢，走缓存）。

    拼齐：分段回报率(各区间收益/最大回撤/同类排名) + 最新净值。
    每个子接口都单独 try/except：某段拿不到就跳过，绝不整体报错；全拿不到返回空串。
    （注：雪球的基金概况/费率接口在部分 akshare 版本有 bug(KeyError:'data')，故此处不接入；
      需要规模/成立日/费率时由 Agent 用 web_search/fetch_url 查官网。）
    """
    now = time.time()
    hit = _detail_cache.get(code)
    if hit and now - hit[0] < _DETAIL_TTL:
        return hit[1]
    try:
        import akshare as ak
    except Exception:  # noqa: BLE001 —— 未安装/依赖缺失
        return ""

    parts: list[str] = []

    # 1) 阶段业绩（分段回报率）—— 雪球：各区间本产品收益 vs 同类
    try:
        df = ak.fund_individual_achievement_xq(symbol=code)
        if df is not None and not df.empty:
            cols = list(df.columns)
            seg_col = next((c for c in ("周期", "业绩类型") if c in cols), cols[0])
            ret_col = next((c for c in ("本产品区间收益", "区间收益") if c in cols), None)
            # 注：akshare 该列拼写为"回撒"，一并兼容规范拼写"回撤"
            dd_col = next((c for c in ("本产品最大回撒", "本产品最大回撤", "最大回撤") if c in cols), None)
            rank_col = next((c for c in ("周期收益同类排名", "同类排名") if c in cols), None)

            def _pct(x) -> str:
                try:
                    return f"{float(x):.2f}%"
                except (TypeError, ValueError):
                    return str(x)

            def _has(x) -> bool:
                return x is not None and str(x).strip().lower() not in ("", "nan", "none")

            segs = []
            for _, r in df.iterrows():
                seg = str(r.get(seg_col, "")).strip()
                if not seg:
                    continue
                ret = _pct(r.get(ret_col)) if ret_col else ""
                bits = []
                if dd_col and _has(r.get(dd_col)):
                    bits.append(f"回撤{_pct(r.get(dd_col))}")
                if rank_col and _has(r.get(rank_col)):
                    bits.append(f"排名{r.get(rank_col)}")
                suffix = f"（{'，'.join(bits)}）" if bits else ""
                segs.append(f"{seg} {ret}{suffix}")
            if segs:
                parts.append("分段回报率（区间收益/最大回撤/同类排名）：" + "；".join(segs))
    except Exception:  # noqa: BLE001
        pass

    # 2) 最新净值 + 最近走势 —— 天天基金：单位净值走势
    try:
        nav = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if nav is not None and not nav.empty:
            last = nav.iloc[-1]
            d = last.get("净值日期", "")
            v = last.get("单位净值", "")
            g = last.get("日增长率", "")
            parts.append(f"最新净值：{v}（{d}），当日涨跌 {g}%")
    except Exception:  # noqa: BLE001
        pass

    text = "\n".join(parts).strip()
    if text:
        text = f"基金 {code} 详情：\n{text}"
        _detail_cache[code] = (now, text)
    return text


# ---------------- 基金名称 → 代码 解析（记录持仓时自动补代码） ----------------

_NAME_MAP_TTL = 86400          # 全市场基金名表缓存 24 小时
_name_map_cache: tuple[float, dict] | None = None
_SPLIT_RE = re.compile(r"[，,、；;。\n]+")
_NAMEISH = ("债", "股", "基金", "混合", "指数", "ETF", "etf", "纯债", "短债",
            "理财", "成长", "价值", "红利", "货币", "债券", "量化", "优选")


def _fund_name_map() -> dict[str, str]:
    """全市场『基金简称 → 6位代码』映射（akshare fund_name_em，缓存 24h）。失败返回空表。"""
    global _name_map_cache
    now = time.time()
    if _name_map_cache and now - _name_map_cache[0] < _NAME_MAP_TTL:
        return _name_map_cache[1]
    try:
        import akshare as ak

        df = ak.fund_name_em()
        cols = set(df.columns)
        code_col = "基金代码" if "基金代码" in cols else None
        name_col = "基金简称" if "基金简称" in cols else None
        mapping: dict[str, str] = {}
        if code_col and name_col:
            for _, r in df.iterrows():
                nm = str(r[name_col]).strip()
                cd = str(r[code_col]).strip()
                if nm and cd and nm not in mapping:
                    mapping[nm] = cd
    except Exception:  # noqa: BLE001 —— 未安装/网络/接口变更
        return _name_map_cache[1] if _name_map_cache else {}
    if mapping:
        _name_map_cache = (now, mapping)
    return mapping


def resolve_fund_code(name: str) -> str | None:
    """按基金名称解析 6 位代码：精确匹配、或『唯一』的包含匹配才返回；含糊(多命中)一律返回 None，绝不瞎猜。"""
    name = (name or "").strip().strip(" ：:（）()【】")
    if len(name) < 3:
        return None
    m = _fund_name_map()
    if not m:
        return None
    if name in m:
        return m[name]
    cands = {c for nm, c in m.items() if name in nm or nm in name}
    return next(iter(cands)) if len(cands) == 1 else None


def enrich_codes_in_text(text: str) -> str:
    """给自由文本里『有基金名、但没带 6 位代码』的持仓自动补上代码（唯一匹配才补，绝不瞎猜）。
    仅在开启实时数据时生效；akshare 不可用时原样返回。补进去的代码形如『基金名(代码)』。"""
    if not settings.enable_live_data or not text:
        return text
    out = text
    seen: set[str] = set()
    for seg in _SPLIT_RE.split(text):
        s = seg.strip()
        if len(s) < 4 or not any(k in s for k in _NAMEISH):
            continue
        # 截掉金额/份额等尾巴，取名字主体
        name = re.split(r"[0-9一二三四五六七八九十两\s]", s, 1)[0].strip(" ：:（）()【】")
        if len(name) < 4:
            name = s
        if name in seen:
            continue
        seen.add(name)
        code = resolve_fund_code(name)
        if code and code not in out:
            out = out.replace(name, f"{name}({code})", 1)
    return out


def fund_detail_text(code: str) -> str:
    """按代码返回单只基金详情文本；代码非法 / 未开实时数据 / 取不到均返回空串。
    供 holdings 等内部逻辑直接复用（不带面向用户的提示语）。"""
    code = re.sub(r"\D", "", code or "")[:6]
    if len(code) != 6 or not settings.enable_live_data:
        return ""
    return _fetch_fund_detail(code)


class FundDetailInput(BaseModel):
    code: str = Field(..., description="6 位基金代码，如 019112")


@tool(args_schema=FundDetailInput)
def query_fund_detail(code: str) -> str:
    """按【基金代码】查单只基金的详细信息：分段回报率(近1月/3月/半年/1年/成立以来等区间收益)、
    最新净值。当用户给了具体基金代码、或需要排行榜里没有的分段业绩/历史净值/详情时调用它
    （排行榜接口只有近1年收益，拿不到分段回报时用这个补齐）。取不到就如实说查不到。"""
    clean = re.sub(r"\D", "", code or "")[:6]
    if len(clean) != 6:
        return "（请提供 6 位基金代码，如 019112。）"
    if not settings.enable_live_data:
        return "（未开启实时数据（ENABLE_LIVE_DATA=false），无法查询基金详情。）"
    text = fund_detail_text(clean)
    return text or f"（暂未取到基金 {clean} 的详情数据，可稍后再试，或用联网搜索查其官网/公告。）"
