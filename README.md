# PyFinAgent · 小白理财智能助手

面向理财小白的一体化平台：**学知识 · 看产品 · 测风险 · 挑产品**。基于 **FastAPI + LangGraph + LangChain** 构建，覆盖投资者教育 RAG、金融产品信息聚合与对比、风险测评、个性化推荐（含投资者适当性）与 SSE 流式响应。

> 本仓库是一个**可运行骨架**：外部依赖（LLM API、MySQL、PostgreSQL/pgvector、Redis）全部可缺省。缺省时以 **mock 模式** 启动——内存 LLM、SQLite、内存向量库、内存 checkpointer，并自动播种内置理财知识库——无需任何配置即可 `uvicorn` 跑通全链路，方便演示与逐块替换为真实实现。

## 四大模块

| 模块 | 说明 | 对应 Agent |
| :--- | :--- | :--- |
| 学知识 | 用小白听得懂的话解释理财概念（基于教育知识库的 Agentic RAG） | `education_agent` |
| 看产品 | 多类产品统一口径的检索与对比（基金/ETF/理财/存款/国债…） | `product_agent` |
| 测风险 | 标准问卷 → 风险画像（谨慎/稳健/平衡/进取/激进）与 R1-R5 | `assessment_agent` |
| 挑产品 | 结合风险画像做**适当性匹配**的可解释配置建议 + 风险揭示 | `recommend_agent` |

本项目定位为**投顾 / 教育**，不接入交易通道、不执行任何申购/赎回操作；涉及"买/卖哪只"的问题只当作咨询，由 `recommend_agent` 给出适当性建议与风险揭示。

## 技术栈

| 层级 | 选型 |
| :--- | :--- |
| Web 框架 | FastAPI + Uvicorn (asyncio) |
| Agent 框架 | LangGraph (StateGraph) + LangChain |
| 校验 | Pydantic v2 |
| 业务库 | MySQL 8 / SQLAlchemy 2.0（缺省 SQLite） |
| 向量库 | PostgreSQL + pgvector（缺省内存向量库） |
| 缓存/记忆 | Redis（缺省内存 checkpointer；教育类答案做语义缓存） |
| 流式 | SSE (sse-starlette) |
| 可靠性 | tenacity + aiolimiter + circuitbreaker |
| 鉴权 | OAuth2 + JWT (python-jose) |

## 目录结构

```
pyfinagent/
├── app/
│   ├── main.py              # FastAPI 入口（启动播种知识库）
│   ├── config.py            # pydantic-settings 配置
│   ├── api/                 # 路由：chat(SSE) / knowledge / auth
│   ├── agents/              # LangGraph 多 Agent 编排
│   │   ├── router.py        # 意图路由（education/product/recommend/assessment/chat）
│   │   ├── education_agent.py / product_agent.py
│   │   ├── assessment_agent.py / recommend_agent.py / chat_agent.py
│   ├── tools/               # 产品检索对比 / 风险测评打分 @tool
│   ├── data/                # 产品数据集 + 教育知识库（mock）
│   ├── rag/                 # 解析 / 切分 / 向量 / 混合检索
│   ├── llm/                 # LLM 封装（含 mock 回退）
│   ├── db/                  # SQLAlchemy 模型与 session
│   ├── memory/              # checkpointer
│   ├── core/                # 鉴权 / 可靠性 / 语义缓存
│   └── schemas/             # Pydantic 请求响应模型
├── sql/                     # MySQL & pgvector DDL
├── scripts/                 # 知识库入库脚本 / 环境自检
└── tests/                   # pytest 冒烟测试
```

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt

# （可选）配置 .env；不配也能以 mock 模式启动
uvicorn app.main:app --reload

# 打开 http://localhost:8000 使用前端，或 http://localhost:8000/docs 看接口
```

## 试跑一次对话（SSE）

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","query":"什么是ETF？和主动基金有什么区别"}'
```

可以依次体验：`什么是定投` → `有哪些 R2 以内的稳健产品` → `帮我做个风险测评`（回复 `A B C C B D`）→ `帮我推荐一个配置方案`。

## 从 mock 切到真实实现

1. **LLM**：`.env` 填 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL`（兼容 DeepSeek、通义千问、OpenAI）。
2. **Embedding**：填 `EMBEDDING_API_KEY`（默认 DashScope `text-embedding-v3`）。
3. **向量库**：填 `PGVECTOR_DSN`，执行 `sql/pgvector_schema.sql`。
4. **业务库**：填 `MYSQL_DSN`，执行 `sql/mysql_schema.sql`（或 Alembic 迁移）。
5. **Redis 记忆/缓存**：填 `REDIS_URL`。
6. **联网取真实数据**：在 `.env` 设 `ENABLE_LIVE_DATA=true`，产品查询会**优先联网取真实数据**——
   先用 `akshare` 公开接口拉真实基金数据（免费无需 key），失败再走联网搜索
   （需配 `WEB_SEARCH_API_KEY`，如 Tavily），再不行才回退内置示例库；回答会标注数据来源。
   也可把 `app/data/products.py` 的示例数据替换为持牌数据供应商 API 并标注来源与更新时间。
7. **真实教育内容**：用 `POST /api/knowledge/ingest_text` 或 `/upload` 导入体系化课程与科普文章。

## 实施路线图

- [x] 阶段一：四大模块可运行骨架（教育 RAG / 产品聚合对比 / 风险测评 / 适当性推荐）
- [ ] 阶段二：真实产品数据接入 + 数据质量监控；教育内容体系化 + Embedding/pgvector
- [ ] 阶段三：LangGraph 原生 `interrupt()` 强化申购确认；组合跟踪与再平衡提醒
- [ ] 阶段四：可观测性（LangSmith）/ 审计日志 / RAG 评估（Ragas / DeepEval）

> ⚠️ 免责声明：本项目为技术演示，内置产品与数据均为模拟，不构成任何投资、法律或财务建议。真实金融业务需遵循相应牌照与监管的适当性、风险揭示与信息披露要求。
