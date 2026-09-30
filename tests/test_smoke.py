"""冒烟测试：验证核心链路在 mock 模式下可跑通，无需任何外部服务。"""
from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.agents.router import classify_intent
from app.tools.assessment_tools import score_answers
from app.tools.product_tools import compare_products, search_products


def test_intent_classification():
    assert classify_intent("什么是ETF？") == "education"
    assert classify_intent("有哪些低风险的基金可以对比") == "product"
    assert classify_intent("帮我做个风险测评") == "assessment"
    assert classify_intent("帮我配置一个适合我的组合方案") == "recommend"
    # 知识型的“……是什么”应归教育，即使句中出现“基金”
    assert classify_intent("基金和股票的区别是什么") == "education"
    # 在对话里更新个人理财数据 → profile
    assert classify_intent("更新我的理财数据：现在有10万定期") == "profile"
    assert classify_intent("记一下我又买了5万货币基金") == "profile"


@pytest.mark.asyncio
async def test_user_finance_private_store():
    """个人理财资料按用户隔离，可增改，互不可见。"""
    import uuid

    from app.db import repository as repo
    from app.db.session import init_db

    await init_db()
    bob = "bob_" + uuid.uuid4().hex[:10]     # 唯一用户名，避免与历史数据冲突
    carol = "carol_" + uuid.uuid4().hex[:10]
    assert await repo.get_user_finance(bob) is None
    await repo.set_user_finance(bob, "货基5万，目标攒首付，月投3000")
    assert "月投3000" in (await repo.get_user_finance(bob) or "")
    await repo.set_user_finance(bob, "更新后的资料")
    assert await repo.get_user_finance(bob) == "更新后的资料"
    assert await repo.get_user_finance(carol) is None   # 隔离


@pytest.mark.asyncio
async def test_risk_profile_persists_across_sessions():
    """测评算出的风险画像按用户持久化：换个会话，推荐仍能读到该用户的风险等级。"""
    import uuid

    from app.agents.graph import build_graph
    from app.db import repository as repo
    from app.db.session import init_db

    await init_db()
    uname = "rk_" + uuid.uuid4().hex[:8]
    graph = build_graph()
    # 会话1：提交测评 → 得到风险画像并持久化
    r1 = await graph.ainvoke(
        {"messages": [HumanMessage(content="风险测评：A B B C B C")], "username": uname},
        config={"configurable": {"thread_id": "rk-s1-" + uname}},
    )
    assert r1["agent_type"] == "assessment_agent" and r1.get("risk_profile")
    saved = await repo.get_user_risk(uname)
    assert saved  # 已落库

    # 会话2（全新 thread、无会话记忆）：注入持久化的风险画像 → 推荐仍可用
    r2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="帮我推荐一个配置方案")],
         "username": uname, "risk_profile": saved},
        config={"configurable": {"thread_id": "rk-s2-" + uname}},
    )
    assert r2["agent_type"] == "recommend_agent"


@pytest.mark.asyncio
async def test_graph_profile_update():
    """在对话中更新'我的理财数据'：路由到 profile_agent，并写入该用户的私有资料。"""
    import uuid

    from app.agents.graph import build_graph
    from app.db import repository as repo
    from app.db.session import init_db

    await init_db()
    uname = "pf_" + uuid.uuid4().hex[:8]
    graph = build_graph()
    r = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="更新我的理财数据：现在有10万定期，每月可投3000")],
            "username": uname,
            "user_finance": "",
        },
        config={"configurable": {"thread_id": "test-profile-" + uname}},
    )
    assert r["agent_type"] == "profile_agent"
    # 已落库（mock 模式下为合并后的占位文本，非空即证明写入成功、且按用户名隔离）
    assert await repo.get_user_finance(uname)
    assert await repo.get_user_finance("someone_else_" + uname) is None


@pytest.mark.asyncio
async def test_chat_sessions_per_user():
    """会话按归属用户隔离：各自只看到自己的历史，最近活跃在前。"""
    import uuid

    from app.db import repository as repo
    from app.db.session import init_db

    await init_db()
    alice = "alice_" + uuid.uuid4().hex[:8]
    bob = "bob_" + uuid.uuid4().hex[:8]
    sa1, sa2, sb1 = ("s_" + uuid.uuid4().hex[:8] for _ in range(3))

    await repo.ensure_session(sa1, title="A的第一次", owner=alice)
    await repo.ensure_session(sa2, title="A的第二次", owner=alice)
    await repo.ensure_session(sb1, title="B的会话", owner=bob)

    a_ids = {s.id for s in await repo.list_sessions(alice)}
    b_ids = {s.id for s in await repo.list_sessions(bob)}
    assert a_ids == {sa1, sa2}      # 只看到自己的
    assert b_ids == {sb1}
    assert sb1 not in a_ids         # 隔离：看不到别人的

    # 再次触碰 sa1 → 活跃时间刷新 → 应排到 alice 列表最前
    await repo.ensure_session(sa1, title="A的第一次", owner=alice)
    newest = (await repo.list_sessions(alice))[0]
    assert newest.id == sa1

    sess = await repo.get_chat_session(sb1)
    assert sess is not None and sess.owner == bob


@pytest.mark.asyncio
async def test_user_register_and_login():
    """密码 bcrypt 哈希 + 用户表增查；校验通过/失败。"""
    from app.core.security import hash_password, verify_password
    from app.db import repository as repo
    from app.db.session import init_db

    import uuid

    await init_db()
    h = hash_password("secret123")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)

    uname = "u_" + uuid.uuid4().hex[:10]   # 唯一用户名，避免与历史数据冲突
    uid = await repo.create_user(uname, h)
    u = await repo.get_user(uname)
    assert u is not None and u.id == uid
    assert verify_password("secret123", u.password_hash)
    assert await repo.get_user("nobody_" + uuid.uuid4().hex) is None


def test_research_intent_and_websearch_nokey():
    from app.config import settings
    from app.tools.web_tools import web_search

    assert classify_intent("搜一下最新的基金新闻") == "research"
    assert classify_intent("查证一下这条政策是不是真的") == "research"
    # 未配置 key 时 web_search 优雅提示，不抛异常
    settings.web_search_api_key = ""
    out = web_search.invoke({"query": "上证指数"})
    assert "未配置" in out


@pytest.mark.asyncio
async def test_graph_research_mock():
    from app.agents.graph import build_graph

    graph = build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="搜一下最近有什么理财新闻")]},
        config={"configurable": {"thread_id": "test-research"}},
    )
    assert result["agent_type"] == "research_agent"


def test_diversify_rows():
    """推荐候选去同质化：同一只基金的 A/C 份额只留一个；每公司限量分散。"""
    from app.tools.live_data import _base_fund_name, _fund_company, diversify_rows

    assert _base_fund_name("华夏鼎利债券C") == "华夏鼎利债券"
    assert _base_fund_name("易方达安心回报债券A") == "易方达安心回报债券"
    # 公司识别：多字公司名整体识别，别被前两字截错
    assert _fund_company("易方达安心回报债券") == "易方达"
    assert _fund_company("工银瑞信双利债券") == "工银瑞信"
    assert _fund_company("东方红睿丰混合") == "东方红"      # 长名优先于"东方"
    rows = [
        {"产品名称": "华夏鼎利债券C", "近1年年化(%)": 6.0},
        {"产品名称": "华夏鼎利债券A", "近1年年化(%)": 5.9},   # 同一只基金另一份额 → 去掉
        {"产品名称": "招商产业债券A", "近1年年化(%)": 5.5},
        {"产品名称": "国泰信用互利债券", "近1年年化(%)": 5.0},
    ]
    out = diversify_rows(rows, limit=8)
    names = [r["产品名称"] for r in out]
    assert "华夏鼎利债券C" in names and "华夏鼎利债券A" not in names   # 只留收益最高的份额
    assert len(out) == 3

    # 每公司限量：候选够填满 limit 时，同公司第 3 只被挤掉、让位给别的公司
    mixed = [
        {"产品名称": "易方达裕祥回报债券", "近1年年化(%)": 6.0},
        {"产品名称": "易方达稳健收益债券", "近1年年化(%)": 5.5},
        {"产品名称": "易方达信用债债券", "近1年年化(%)": 5.0},   # 同公司第 3 只 → 挤掉
        {"产品名称": "招商产业债券", "近1年年化(%)": 4.5},
        {"产品名称": "南方多利债券", "近1年年化(%)": 4.0},
    ]
    names2 = [r["产品名称"] for r in diversify_rows(mixed, limit=3, per_company=2)]
    assert len(names2) == 3
    assert "易方达信用债债券" not in names2   # 同公司第 3 只被限量挤掉
    assert "招商产业债券" in names2            # 让位给了别的公司


def test_web_compress():
    """网页正文按问题压缩：只留命中关键词的段落，无关的丢弃。"""
    from app.tools.web_tools import _compress

    text = "今天天气不错。这只基金近1年收益率是5.8%。作者还聊了些别的闲话。费率是0.3%。"
    out = _compress(text, "这只基金近1年收益和费率", max_chars=200)
    assert "5.8%" in out and "费率是0.3%" in out
    assert "天气" not in out   # 无关段落被丢


def test_purchase_words_route_to_recommend():
    """本项目不接交易，也无"申购确认"：申购/购买类问法只当咨询，路由到 recommend 给建议。"""
    assert classify_intent("我想申购一只稳健的基金") == "recommend"
    assert classify_intent("这几只我该买哪只") == "recommend"


def test_holding_query_detection():
    """"查我自己持仓的现状/收益" → 走 holding；"评价我的持仓"(要建议) 不误入 holding。"""
    from app.agents.router import has_holding_query, router_node

    assert has_holding_query("找一下我目前买的这四个产品的利益近况")
    assert has_holding_query("我的持仓收益怎么样")
    assert not has_holding_query("帮我评价我的持仓")   # 要建议 → recommend，不是查现状
    assert not has_holding_query("有哪些低风险基金")


@pytest.mark.asyncio
async def test_router_routes_holding():
    from app.agents.router import router_node

    out = await router_node({"messages": [HumanMessage(content="我买的这几只基金现在收益如何")]})
    assert out["intent"] == "holding"


@pytest.mark.asyncio
async def test_graph_holdings_no_codes():
    """持仓资料里没有基金代码时，holding 节点如实提示补充代码，不编造。"""
    graph = build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="我买的这几个产品收益怎么样")],
         "user_finance": "十五万定期、三万余额宝、五万某基金（无代码）"},
        config={"configurable": {"thread_id": "test-holding"}},
    )
    assert result["agent_type"] == "holdings_agent"
    assert result["messages"][-1].content


def test_product_search_tool():
    out = search_products.invoke({"max_risk_level": 2, "limit": 5})
    assert out["count"] >= 1
    # 适当性：不返回超过 R2 的产品
    assert all(p["风险等级"] <= 2 for p in out["products"])


def test_product_compare_tool():
    out = compare_products.invoke({"codes": ["IDX001", "BOND001"]})
    assert len(out["compared"]) == 2
    assert not out["missing"]


def test_assessment_scoring():
    conservative = score_answers(["A", "A", "A", "A", "A", "A"])
    assert conservative["ok"] and conservative["max_risk_level"] == 1
    aggressive = score_answers(["D", "D", "D", "D", "D", "D"])
    assert aggressive["ok"] and aggressive["max_risk_level"] == 5


@pytest.mark.asyncio
async def test_graph_education():
    graph = build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="什么是基金？")]},
        config={"configurable": {"thread_id": "test-edu"}},
    )
    assert result["agent_type"] == "education_agent"
    assert result["messages"][-1].content


def test_rate_limit(monkeypatch):
    """按用户限流：超过每分钟上限后拒绝；不同用户互不影响。"""
    from app.config import settings
    from app.core import ratelimit

    monkeypatch.setattr(settings, "rate_limit_per_min", 3)
    monkeypatch.setattr(settings, "rate_limit_per_day", 0)
    ratelimit._minute.clear()
    u = "rl_user"
    assert all(ratelimit.check_and_consume(u)[0] for _ in range(3))   # 前3次放行
    ok, why = ratelimit.check_and_consume(u)
    assert not ok and "频繁" in why                                    # 第4次被拦
    assert ratelimit.check_and_consume("other_user")[0]               # 别的用户不受影响


def test_history_summary_injection():
    """长对话摘要存在时作为系统提示置顶注入；没有摘要则只给最近消息。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from app.agents.state import history_for_prompt

    st = {"messages": [HumanMessage(content="q1"), AIMessage(content="a1"),
                       HumanMessage(content="q2")], "summary": ""}
    h = history_for_prompt(st, 8)
    assert all(not isinstance(m, SystemMessage) for m in h)   # 无摘要不插系统提示
    st["summary"] = "用户有10万定期，目标3年攒首付"
    h2 = history_for_prompt(st, 8)
    assert isinstance(h2[0], SystemMessage) and "攒首付" in h2[0].content


@pytest.mark.asyncio
async def test_memory_node_summarizes_long_convo(monkeypatch):
    """长对话触发摘要（非 mock 时）；短对话跳过。"""
    from langchain_core.messages import AIMessage, HumanMessage

    import app.agents.memory_agent as mem
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-fake")  # 关闭 mock 门槛

    async def fake(llm, prompt, *a, **k):
        return AIMessage(content="摘要：用户在连续咨询理财配置")
    monkeypatch.setattr(mem, "ainvoke_llm", fake)

    short = {"messages": [HumanMessage(content="hi")], "summary": None, "summary_at": None}
    assert await mem.memory_node(short) == {}   # 太短，跳过

    msgs = [HumanMessage(content=f"问题{i}") if i % 2 == 0 else AIMessage(content=f"回答{i}")
            for i in range(14)]
    out = await mem.memory_node({"messages": msgs, "summary": None, "summary_at": None})
    assert out.get("summary") and out.get("summary_at") == 14


def test_multi_intent_plan():
    """'先学后挑/看'类问题识别为多意图；纯概念问不误触发。"""
    from app.agents.router import plan_intents

    assert plan_intents("什么是ETF，有没有适合我的") == ["education", "recommend"]
    assert plan_intents("基金和股票的区别是什么，有哪些可以对比") == ["education", "product"]
    assert plan_intents("什么是理财") == ["education"]          # 泛化名词不误触发多意图
    assert plan_intents("帮我推荐一个配置方案") == ["recommend"]


@pytest.mark.asyncio
async def test_graph_multi_intent():
    """多意图 → 编排节点 multi_agent，合并教育+推荐两段回答。"""
    graph = build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="什么是ETF，有没有适合我的")]},
        config={"configurable": {"thread_id": "test-multi"}},
    )
    assert result["agent_type"] == "multi_agent"
    assert result["messages"][-1].content


@pytest.mark.asyncio
async def test_graph_product():
    graph = build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="有哪些低风险的理财产品")]},
        config={"configurable": {"thread_id": "test-prod"}},
    )
    assert result["agent_type"] == "product_agent"


@pytest.mark.asyncio
async def test_feedback_record():
    """点赞/点踩满意度反馈可落库并读回。"""
    from app.db import repository as repo
    from app.db.session import init_db

    await init_db()
    fid = await repo.save_feedback(
        "s-fb", "什么是货币基金", "货基是……", "up", agent_type="education_agent"
    )
    fb = await repo.get_feedback(fid)
    assert fb is not None and fb.rating == "up"


def test_live_data_disabled_returns_mock(monkeypatch):
    from app.config import settings
    from app.tools import live_data

    monkeypatch.setattr(settings, "enable_live_data", False)
    rows, source = live_data.fetch_live_products("有哪些基金")
    assert rows is None and source == "mock"


def test_live_data_fallback_chain(monkeypatch):
    """验证降级链：akshare → 联网搜索 → mock。"""
    from app.config import settings
    from app.tools import live_data

    monkeypatch.setattr(settings, "enable_live_data", True)

    # 方法1 成功
    monkeypatch.setattr(live_data, "_akshare_funds", lambda q, mr, l: [{"产品名称": "某真实基金"}])
    rows, source = live_data.fetch_live_products("基金")
    assert source == "akshare" and rows

    # 方法1 失败 → 方法2 成功
    monkeypatch.setattr(live_data, "_akshare_funds", lambda q, mr, l: None)
    monkeypatch.setattr(live_data, "_web_search", lambda q, l: [{"产品名称": "网络结果"}])
    rows, source = live_data.fetch_live_products("基金")
    assert source == "web" and rows

    # 两者都失败 → 回退 mock
    monkeypatch.setattr(live_data, "_web_search", lambda q, l: None)
    rows, source = live_data.fetch_live_products("基金")
    assert rows is None and source == "mock"


@pytest.mark.asyncio
async def test_graph_assessment_then_recommend():
    graph = build_graph()
    # 先提交测评作答 → 应得到风险画像
    r1 = await graph.ainvoke(
        {"messages": [HumanMessage(content="风险测评：A B B C B C")]},
        config={"configurable": {"thread_id": "test-flow"}},
    )
    assert r1["agent_type"] == "assessment_agent"
    assert r1.get("risk_profile")
    # 再请求推荐（同一会话，带记忆）→ 推荐 Agent
    r2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="那帮我推荐一个配置方案")]},
        config={"configurable": {"thread_id": "test-flow"}},
    )
    assert r2["agent_type"] == "recommend_agent"
