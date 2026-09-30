# PyFinAgent · 小白理财智能助手

面向理财小白的对话式理财助手：**学知识 · 看产品 · 测风险 · 挑产品 · 查持仓**。基于 **FastAPI + LangGraph + LangChain** 构建多 Agent 协作，接入 akshare 真实公募基金数据，按投资者适当性给出可解释的配置建议，全程 SSE 流式输出。

本项目定位为**投顾 / 教育**，不接入交易通道、不执行任何申购/赎回操作；涉及"买/卖哪只"的问题只当作咨询，由 `recommend_agent` 给出适当性建议与风险揭示。

## 功能模块

| 模块 | 说明 | 对应 Agent |
| :--- | :--- | :--- |
| 学知识 | 用小白听得懂的话解释理财概念（大模型直接作答，不编造具体产品数字） | `education_agent` |
| 看产品 | 基金查询与对比，ReAct 工具调用取 akshare 实时数据 | `product_agent` |
| 测风险 | 标准问卷 → 风险画像（谨慎/稳健/平衡/进取/激进）与 R1-R5，跨会话持久化 | `assessment_agent` |
| 挑产品 | 按风险等级筛选候选 + 去同质化，给出可解释配置建议与风险揭示 | `recommend_agent` |
| 查最新 | 联网搜索新闻 / 政策 / 公告，附来源链接 | `research_agent` |
| 查持仓 | 读取用户保存的持仓，按基金代码查最新净值与区间收益 | `holdings_agent` |
| 记资料 | 对话中更新个人理财资料；随口提到的持仓/目标由后台静默抽取 | `profile_agent` |
| 复合问题 | "先解释概念再推荐/看产品"类问题拆成多个意图依次执行后合并 | `multi_agent` |

## 架构

```
START → memory（长对话滚动摘要）→ router（意图路由）→ 条件边 → 业务 Agent → END
```

- **意图路由**：关键词打分 + 优先级决胜；未命中时默认走知识问答，可通过 `ROUTER_LLM_FALLBACK=true` 改为大模型分类。
- **会话记忆**：LangGraph checkpointer（配置 Redis 时持久化，否则进程内存）。
- **实时数据**：akshare → Tavily 联网搜索逐级降级；基金排行后台定时预热 + stale-while-revalidate 缓存。
- **可靠性**：LLM 调用统一令牌桶限流 + 熔断 + 指数退避重试；按用户滑动窗口限流与每日配额。

## 技术栈

| 层级 | 选型 |
| :--- | :--- |
| Web 框架 | FastAPI + Uvicorn (asyncio) |
| Agent 框架 | LangGraph (StateGraph) + LangChain |
| 大模型 | OpenAI 兼容协议（DeepSeek / 通义千问 / OpenAI） |
| 校验 | Pydantic v2 / pydantic-settings |
| 业务库 | MySQL 8 / SQLAlchemy 2.0 异步（缺省 SQLite） |
| 会话记忆 / 缓存 | Redis（缺省内存 checkpointer；教育类答案精确匹配缓存） |
| 数据源 | akshare 公募基金数据 + Tavily 联网搜索 |
| 流式 | SSE (sse-starlette) |
| 可靠性 | tenacity + aiolimiter + circuitbreaker |
| 鉴权 | OAuth2 + JWT (python-jose) + bcrypt |

## 目录结构

```
pyfinagent/
├── app/
│   ├── main.py              # FastAPI 入口（启动时建表、初始化 checkpointer、预热行情缓存）
│   ├── config.py            # pydantic-settings 配置
│   ├── api/                 # 路由：chat(SSE) / auth / profile / feedback
│   ├── agents/              # LangGraph 多 Agent 编排
│   │   ├── graph.py         # 状态图组装
│   │   ├── router.py        # 意图路由
│   │   ├── memory_agent.py  # 长对话滚动摘要
│   │   ├── react.py         # 可复用的 ReAct 工具调用循环
│   │   └── *_agent.py       # 各业务 Agent
│   ├── tools/               # akshare 实时数据 / 联网搜索 / 产品检索 / 风险测评打分
│   ├── data/                # 内置示例产品库（仅测试使用）
│   ├── llm/                 # LLM 封装（含测试用 Mock LLM）
│   ├── db/                  # SQLAlchemy 模型、session 与 repository
│   ├── memory/              # checkpointer
│   ├── core/                # 鉴权 / 可靠性 / 限流 / 缓存
│   ├── schemas/             # Pydantic 请求响应模型
│   └── static/              # 前端聊天页面
├── sql/                     # MySQL 建表 DDL
├── scripts/                 # 环境自检脚本（LLM / DB / Redis / 基金详情）
├── bench/                   # 性能与效果评测（路由准确率 / 首字延迟 / Token）
└── tests/                   # pytest 冒烟测试（离线可跑）
```

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env   # 至少填 OPENAI_API_KEY，否则服务拒绝启动
uvicorn app.main:app --reload

# 打开 http://localhost:8000 使用前端，或 http://localhost:8000/docs 看接口
```

或用 Docker Compose 一键启动应用 + MySQL + Redis，见 [DEPLOY.md](DEPLOY.md)。

## 配置项

| 变量 | 作用 | 缺省行为 |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL` | 大模型 | **必填**，未配置时拒绝启动 |
| `MYSQL_DSN` | 业务库 | SQLite 文件 `./data/pyfinagent.db` |
| `REDIS_URL` | 会话记忆持久化 + 答案缓存 | 进程内存 checkpointer，不缓存 |
| `ENABLE_LIVE_DATA` | 开启 akshare 实时基金数据 | 关闭，产品类问题如实回答"查不到" |
| `WEB_SEARCH_API_KEY` | Tavily 联网搜索 | 跳过联网搜索 |
| `AUTH_ENABLED` / `JWT_SECRET` | 接口鉴权 | 关闭（匿名用户）；开启时须设置强随机密钥 |
| `ROUTER_LLM_FALLBACK` | 关键词判不出意图时用大模型分类 | 关闭，默认走知识问答 |
| `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_PER_DAY` | 每用户限流与配额 | 15 次/分钟、300 次/天 |

## 试跑一次对话（SSE）

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","query":"什么是ETF？和主动基金有什么区别"}'
```

可以依次体验：`什么是定投` → `有哪些 R2 以内的稳健产品` → `帮我做个风险测评`（回复 `A B C C B D`）→ `帮我推荐一个配置方案` → `记一下我买了 005273 两万` → `我买的基金最近收益怎么样`。

## 测试

```bash
pytest          # 冒烟测试：使用 Mock LLM，无需 API key / MySQL / Redis
```

Mock LLM 仅用于离线测试；运行服务时必须配置真实大模型，避免给用户返回占位答案。性能评测见 [bench/README.md](bench/README.md)。

## 路线图

- [x] 多 Agent 骨架：教育 / 产品 / 测评 / 适当性推荐 / 联网研究 / 持仓查询 / 资料更新
- [x] akshare 真实基金数据接入 + 后台预热缓存
- [ ] 对接持牌数据源，获取官方产品风险等级
- [ ] LangGraph `interrupt()` 实现人工确认；组合跟踪与再平衡提醒
- [ ] 可观测性（LangSmith）与回答质量评估

> ⚠️ 免责声明：本项目为技术演示，不构成任何投资、法律或财务建议。真实金融业务需遵循相应牌照与监管的适当性、风险揭示与信息披露要求。
