"""联网工具（供 LLM 自己调用）：web_search（Tavily）+ fetch_url。

给 tool-calling / ReAct agent 用：LLM 自己决定搜什么、读哪个链接。
所有网络调用都被包住，失败返回一段说明文本（而不是抛异常），让 LLM 能据此继续或如实说明。
⚠️ 抓回来的网页内容属于"外部数据"，不可当作指令执行（防 prompt injection）——
   由 research_agent 的系统提示统一约束。
"""
from __future__ import annotations

import re

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import settings

_UA = "Mozilla/5.0 (compatible; PyFinAgent/0.2; +https://example.com/bot)"


class WebSearchInput(BaseModel):
    query: str = Field(..., description="要在互联网上搜索的关键词或问题")


class FetchUrlInput(BaseModel):
    url: str = Field(..., description="要抓取并提取正文的网页 URL")
    query: str = Field(
        "", description="用户的问题或关键词；传入后只返回与之最相关的正文段落（压缩后更省 token、更聚焦），强烈建议传"
    )


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", " ", html)
    return re.sub(r"\s+", " ", html).strip()


_STOP = {"什么", "怎么", "哪些", "可以", "一下", "这个", "那个", "我们", "以及", "关于", "如何",
         "是不是", "有没有", "的话", "一个", "多少", "为什么", "怎样"}


def _keywords(text: str) -> list[str]:
    """从问题里抽关键词用于相关段落打分。中文无空格，按 2-gram 切（覆盖"收益/费率"这类被长串埋住的词），
    英文/数字按整词。去停用词。"""
    text = text or ""
    latin = re.findall(r"[A-Za-z0-9.]{2,}", text)
    grams = []
    for run in re.findall(r"[一-龥]+", text):
        grams.extend(run[i:i + 2] for i in range(len(run) - 1))   # 相邻两字滑窗
    toks = latin + grams
    return [t for t in dict.fromkeys(toks) if t not in _STOP][:24]


def _compress(text: str, query: str, max_chars: int = 2000) -> str:
    """按问题相关性压缩正文：切成段落/句子，挑与关键词命中最多的若干段（保持原文顺序），
    截到 max_chars。抽不出关键词/无命中时退化为直接截断。"""
    kws = _keywords(query)
    if not kws:
        return text[:max_chars]
    parts = [p for p in re.split(r"(?<=[。！？!?\n])", text) if p.strip()]
    scored = [(sum(p.count(k) for k in kws), i, p) for i, p in enumerate(parts)]
    scored = [x for x in scored if x[0] > 0]
    if not scored:
        return text[:max_chars]
    scored.sort(key=lambda x: (-x[0], x[1]))     # 命中多的优先，同分按原序
    picked_idx, total = [], 0
    for _score, i, p in scored:
        if total + len(p) > max_chars:
            continue
        picked_idx.append(i)
        total += len(p)
    picked_idx.sort()                             # 复原成原文顺序，读起来连贯
    return "".join(parts[i] for i in picked_idx) or text[:max_chars]


@tool(args_schema=WebSearchInput)
def web_search(query: str) -> str:
    """联网搜索，返回若干条相关网页的标题、链接和摘要。用于获取实时或外部信息（新闻/公告/政策等）。"""
    if not settings.web_search_api_key:
        return "（未配置搜索 API key，无法联网搜索。请在 .env 设置 WEB_SEARCH_API_KEY。）"
    try:
        import httpx

        r = httpx.post(
            settings.web_search_url,
            json={"api_key": settings.web_search_api_key, "query": query,
                  "max_results": 5, "search_depth": "basic", "include_answer": False},
            timeout=20,
        )
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    except Exception as e:  # noqa: BLE001
        return f"（搜索失败：{type(e).__name__}）"
    if not results:
        return "（没有搜到相关结果。）"
    blocks = []
    for i, it in enumerate(results[:5], 1):
        blocks.append(
            f"[{i}] {it.get('title', '')}\n链接: {it.get('url', '')}\n"
            f"摘要: {(it.get('content') or '')[:500]}"
        )
    return "\n\n".join(blocks)


@tool(args_schema=FetchUrlInput)
def fetch_url(url: str, query: str = "") -> str:
    """抓取指定网页并提取正文文本。通常在 web_search 拿到链接后，用它读取该页详情。
    传入 query（用户问题/关键词）时，只返回与问题最相关的正文段落，省 token、更聚焦。"""
    if not re.match(r"^https?://", url or ""):
        return "（无效 URL。）"
    try:
        import httpx

        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": _UA})
        r.raise_for_status()
        html = r.text
    except Exception as e:  # noqa: BLE001
        return f"（抓取失败：{type(e).__name__}）"
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(html) or ""
    except Exception:  # noqa: BLE001 —— 未安装 trafilatura 或解析失败
        text = ""
    if not text:
        text = _strip_html(html)
    text = text.strip()
    if not text:
        return "（未能提取正文。）"
    if query:                                    # 按问题相关性压缩，只留相关段落
        return _compress(text, query, max_chars=2000)
    return (text[:4000] + " …") if len(text) > 4000 else text


def web_search_context(query: str, max_results: int = 4) -> str:
    """供 product/recommend 复用的联网搜索：返回可读的市场信息文本。

    未配置 key / 失败 / 无结果时返回空串（调用方据此决定是否附加联网信息）。
    """
    if not settings.web_search_api_key:
        return ""
    try:
        import httpx

        r = httpx.post(
            settings.web_search_url,
            json={"api_key": settings.web_search_api_key, "query": query,
                  "max_results": max_results, "search_depth": "basic", "include_answer": False},
            timeout=15,
        )
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return ""
    if not results:
        return ""
    return "\n".join(
        f"- {it.get('title', '')}（来源：{it.get('url', '')}）：{(it.get('content') or '')[:280]}"
        for it in results[:max_results]
    )


WEB_TOOLS = [web_search, fetch_url]
