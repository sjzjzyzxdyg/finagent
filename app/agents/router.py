"""意图路由 Agent（Supervisor）。

面向小白理财平台的五类意图：
    education  学知识 —— 概念、原理、名词解释（走教育知识库 RAG）
    product    看产品 —— 查询/对比金融产品信息
    recommend  挑产品 —— 个性化配置/推荐（结合风险画像，做适当性）
    assessment 测风险 —— 风险承受能力测评
    chat       闲聊 / 关于本次对话本身的问题

混合策略：先用关键词快速判断（快、免费、可测），判不出时再用大模型分类；
离线 mock 模式下兜底走 education。
"""
from __future__ import annotations

from app.agents.state import AgentState, last_human
from app.config import settings

INTENTS = ("profile", "holding", "education", "product", "recommend", "assessment", "research", "chat")

# 查"我自己持仓的现状/收益"：需同时命中"指向我的持仓"和"问现状/收益"两类词，
# 以区别于 recommend 的"评价/建议"。命中则读【我的理财数据】按代码查实时收益。
_HOLDING_REF = ["我的持仓", "我买的", "我持有", "我目前买", "我现在买", "我已经买", "我的基金",
                "我的组合", "我的产品", "我手里", "我持的", "持有的这", "买的这", "我的这几",
                "我目前持", "我现在持", "我买了这"]
_HOLDING_STATUS = ["收益", "近况", "现状", "怎么样", "怎样", "表现", "涨", "跌", "赚", "亏",
                   "情况", "最新", "净值", "回报", "如何", "值多少", "盈亏"]


def has_holding_query(text: str) -> bool:
    """是否在问"我自己持仓的最新收益/现状"（读持仓 → 按代码查实时数据）。"""
    return any(a in text for a in _HOLDING_REF) and any(b in text for b in _HOLDING_STATUS)

_KEYWORDS = {
    # 在对话中更新「我的理财数据」（写入私有资料）——需具体指向"我的资料/持仓/理财数据"，或明确的"记录/保存"指令
    "profile": ["更新我的理财", "修改我的理财", "改一下我的理财", "更新我的资料", "修改我的资料",
                "更新我的持仓", "更新我的数据", "更新一下我的理财", "把我的理财数据", "记一下我",
                "记录我的持仓", "帮我记", "补充我的资料", "补充我的持仓", "存进我的理财数据",
                "记到我的理财", "录入我的理财数据", "更新理财数据",
                # 明确的"记录/保存我这些信息"指令（如"这是我目前的投资情况，请你记录"）
                "请你记录", "请记录", "帮我记录", "记录一下", "记下来", "记一下", "帮我记下",
                "登记", "录入", "保存我的", "存一下我的", "记录我的投资", "记录我的资料",
                "这是我的投资", "这是我目前的投资", "这是我的持仓", "这是我目前的持仓"],
    "assessment": ["测评", "风险测评", "风险承受", "承受能力", "问卷", "我的风险等级", "适当性", "评估我的"],
    "recommend": ["推荐", "配置", "适合我", "怎么配", "怎么买", "该买", "帮我选", "组合", "方案", "规划", "建议买", "怎么分配",
                  # 申购/购买/赎回类问法：本项目不接交易，只当作"要不要买/怎么买"的咨询，交给推荐 Agent 给建议
                  "购买", "申购", "认购", "买入", "想买", "要买", "赎回",
                  # 针对"评价我当前的理财/持仓"这类个人化诉求：交给能读【我的理财数据】的推荐 Agent
                  "看法", "怎么看", "评价我", "点评", "复盘", "分析我", "我目前", "我现在的", "我的持仓", "我的资产", "我的理财", "我的情况", "看看我"],
    "research": ["最新", "新闻", "公告", "政策", "搜一下", "搜索", "联网", "网上查", "近期消息", "查证",
                 "是不是真的", "官方通知", "最近", "热点", "帮我查查", "网上"],
    "product": ["产品", "基金", "etf", "理财", "存款", "大额存单", "国债", "债券", "货基", "货币基金",
                "收益率", "费率", "起投", "门槛", "对比", "比较", "有哪些", "筛选", "流动性"],
    "education": ["什么是", "是什么", "科普", "概念", "区别", "解释", "名词", "怎么理解", "原理", "知识",
                  "入门", "术语", "为什么", "怎么算", "定投是", "复利", "分散投资"],
    "chat": ["你好", "您好", "hi", "hello", "你是谁", "你能做", "能做什么", "帮助", "怎么用", "谢谢", "介绍一下自己"],
}


# 同分时的优先级（高 → 低）：更新个人资料是最明确的写操作，排最前；
# 其后测评/推荐属更具体的诉求，应盖过泛化的产品/教育词
_PRIORITY = ["profile", "assessment", "recommend", "research", "education", "product", "chat"]


# 多意图识别：仅覆盖最常见的"先学后挑/看"组合（教育 + 推荐/产品）。
# 用**具体动作短语**判定，避免"什么是理财"里的泛化名词"理财"误触发多意图。
_EDU_ASK = ["什么是", "是什么", "区别", "什么意思", "怎么理解", "原理", "为什么", "科普", "定投是", "复利"]
_ADVICE_ACTION = ["适合我", "推荐", "该买", "帮我选", "怎么配", "怎么分配", "配置", "组合", "方案", "怎么买"]
_PRODUCT_ACTION = ["有哪些", "对比", "比较", "筛选"]


def plan_intents(text: str) -> list[str]:
    """返回本轮要依次执行的意图计划（1 或 2 个）；全不命中返回 []（交给 LLM/兜底）。"""
    kw = _keyword_intent(text)
    if kw is None:
        return []
    if kw == "profile":
        return ["profile"]
    t = text.lower()
    edu = any(s in t for s in _EDU_ASK)
    adv = any(s in t for s in _ADVICE_ACTION)
    prod = any(s in t for s in _PRODUCT_ACTION)
    # 既"问概念"又"要挑/看" → 先教育解释、再推荐/产品
    if edu and (adv or prod):
        return ["education", "recommend" if adv else "product"]
    return [kw]


def _keyword_intent(text: str) -> str | None:
    """关键词分类：命中最高分返回意图；同分按 _PRIORITY 决胜；全不命中返回 None。"""
    text = text.lower()
    scores = {
        intent: sum(1 for kw in kws if kw in text)
        for intent, kws in _KEYWORDS.items()
    }
    # profile 是明确的"更新我的资料"写指令，一旦命中就优先——即使句中也提到了产品名
    # （如"记一下我买了5万货币基金"里的"基金"），也应去更新资料而非查产品。
    if scores.get("profile", 0) > 0:
        return "profile"
    best = max(_PRIORITY, key=lambda i: (scores[i], -_PRIORITY.index(i)))
    return best if scores[best] > 0 else None


def classify_intent(text: str) -> str:
    """纯关键词分类；全不命中默认 education（供测试/兜底用）。"""
    return _keyword_intent(text) or "education"


_CLASSIFY_PROMPT = (
    "你是意图分类器。把用户的最新问题归入下列之一，只回一个英文单词：\n"
    "profile = 想记录/更新/修改自己保存的个人理财数据（如'更新我的理财数据：…''记一下我又买了X''把我的持仓改成…'）；\n"
    "education = 询问理财知识、概念、名词、原理（如'什么是ETF''定投是什么'）；\n"
    "product = 查询或对比具体金融产品信息（如'有哪些低风险基金''对比这两只'）；\n"
    "recommend = 想要个性化推荐/资产配置建议（如'适合我的组合''帮我配置'）；\n"
    "assessment = 想做风险测评、了解自己的风险承受能力；\n"
    "research = 需要联网查实时/外部信息（最新新闻、公告、政策、近期消息、'搜一下''查证'等）；\n"
    "chat = 打招呼、闲聊、或关于本次对话本身的问题。\n"
    "只输出一个单词，不要解释。"
)


async def llm_classify_intent(query: str) -> str:
    """用大模型分类；解析失败兜底 education。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.core.reliability import ainvoke_llm
    from app.llm.provider import get_utility_model

    msg = await ainvoke_llm(
        get_utility_model(),
        [SystemMessage(content=_CLASSIFY_PROMPT), HumanMessage(content=query)],
    )
    ans = (getattr(msg, "content", "") or "").strip().lower()
    for intent in INTENTS:
        if intent in ans:
            return intent
    return "education"


async def router_node(state: AgentState) -> dict:
    last = last_human(state)

    # "查我自己持仓的现状/收益" → 持仓查询节点（读【我的理财数据】按代码查实时收益）
    # 放在最前：要盖过 recommend 的"我的持仓/我目前"等泛化词（那些是给建议、这里是查现状）
    if has_holding_query(last):
        return {"intent": "holding", "intents": ["holding"]}

    plan = plan_intents(last)
    if plan:
        # 多意图 → 走编排节点 multi；单意图 → 直达对应 Agent
        return {"intent": "multi" if len(plan) > 1 else plan[0], "intents": plan}

    # 关键词判不出的模糊问题：默认直接走知识问答（education 可立即流式、也是友好兜底并会引导用户），
    # 不再为分类多等一次大模型往返，从而缩短"思考时间"。需要更精确的意图识别时可开 ROUTER_LLM_FALLBACK。
    if settings.router_llm_fallback and not settings.llm_mock_mode:
        intent = await llm_classify_intent(last)
        return {"intent": intent, "intents": [intent]}
    return {"intent": "education", "intents": ["education"]}


def route_intent(state: AgentState) -> str:
    """条件边：把 intent 映射到目标节点名。"""
    return state.get("intent") or "education"
