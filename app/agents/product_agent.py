"""金融产品信息 Agent（工具调用 / ReAct，双管齐下）。

给 LLM 三件工具，让它自己组合取数后回答"查产品"类问题：
    query_funds  —— akshare 真实基金的结构化数字（收益/费率/风险/门槛），取不到就如实说查不到（默认不兜底）；
    web_search   —— 联网搜索，找官网 / 新闻 / 政策；
    fetch_url    —— 抓取网页正文（包括官网、中国理财网等页面），读取详情。
产品的具体数字优先用 query_funds；市场背景 / 最新动态 / 官网信息用 web_search + fetch_url。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage

from app.agents.react import run_react
from app.agents.state import ANSWER_STYLE, AgentState, history_for_prompt, last_human
from app.config import settings
from app.tools.live_data import query_fund_detail, query_funds
from app.tools.web_tools import fetch_url, web_search

PRODUCT_TOOLS = [query_funds, query_fund_detail, web_search, fetch_url]

SYSTEM_PROMPT = (
    "你是理财产品信息助手，面向小白用户。你可以调用四件工具自己去取数：\n"
    "- query_funds：按条件查真实基金列表的结构化数字（名称/近1年收益/费率/风险/门槛），产品的具体数字以它为准；\n"
    "- query_fund_detail：按【基金代码】查单只基金的详情——分段回报率(近1月/3月/半年/1年/成立以来等区间收益，"
    "含最大回撤与同类排名)、最新净值。当用户给了具体代码、或需要 query_funds 列表里没有的分段业绩/历史净值时用它；"
    "需要规模/成立日/费率时改用 web_search + fetch_url 查官网；\n"
    "- web_search：联网搜索，用来找产品官网、最新新闻、政策动态；\n"
    "- fetch_url：抓取某个网页正文（如基金公司官网、中国理财网、财经媒体），读取详情；"
    "调用时把用户的问题/关键词一并传入 query 参数，只取相关正文、更省更准。\n"
    "做法：查列表用 query_funds；用户点名某只基金（有代码）或要分段业绩/详情时用 query_fund_detail；"
    "涉及'最新/官网/动态/是不是真的'再用 web_search 找权威链接、必要时 fetch_url 打开读。"
    "尽量高效，1–2 次工具就够，别反复调。\n"
    "作答：用统一口径讲清每个产品的关键指标并解释对小白的意义，结合联网信息补充市场背景，"
    "引用联网信息时给出来源链接（Markdown [标题](URL)）。数字只用工具返回的，不得编造；"
    "必须提示过往收益不代表未来、应结合自身风险承受能力选择、不构成投资建议。"
)


async def product_node(state: AgentState) -> dict:
    query = last_human(state)
    finance = (state.get("user_finance") or "").strip()
    q = (
        f"【该用户在'我的理财数据'里保存的私有资料，可结合其持仓/目标作答；"
        f"你能看到它，别说无法访问用户数据】：{finance}\n\n用户问题：{query}"
        if finance else query
    )
    history = history_for_prompt(state, 6)
    answer = await run_react(
        SYSTEM_PROMPT + ANSWER_STYLE, PRODUCT_TOOLS, history, q,
        max_steps=settings.react_max_steps,
    )
    return {"messages": [AIMessage(content=answer)], "agent_type": "product_agent"}
