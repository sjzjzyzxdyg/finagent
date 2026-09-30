# 性能与效果评测方案

目标：给简历/面试产出**可复现、有对照组**的量化数据。所有脚本在项目根目录下以 `python -m bench.xxx` 运行。

## 指标总览

| # | 指标 | 脚本 | 需要的环境 | 对照方式 |
|---|---|---|---|---|
| 1 | 意图路由准确率 / 各意图 F1 / 关键词未命中率 | `eval_router.py` | 无（离线） | 纯关键词 vs 关键词+LLM 兜底 |
| 2 | 推荐链路数据准备耗时 | `bench_components.py` | `ENABLE_LIVE_DATA=true`（+ `WEB_SEARCH_API_KEY`） | 冷/热缓存 × 串行/并行，四组 |
| 3 | 端到端首字延迟 TTFT / 总耗时 | `bench_latency.py stream` | 服务已启动、限流关闭 | 按 Agent 分组；可前后两版各跑一次 |
| 4 | 单轮 Token 消耗 | `bench_latency.py stream`（同上） | 同上 | 按 Agent 分组 |
| 5 | 精确缓存命中收益 | `bench_latency.py cache` | 同上 + `REDIS_URL` | 同一问题：未命中 vs 命中 |

## 1. 意图路由准确率

```bash
python -m bench.eval_router          # 线上默认配置
python -m bench.eval_router --llm    # 打开 LLM 兜底，对比准确率提升和耗时代价
```

- 数据集 `intent_cases.jsonl`：81 条，覆盖 8 个单意图和 2 种多意图组合，分成 `typical`（常规问法）和 `hard`（口语化、没有明显关键词、跨意图）两部分。
- **注意偏差**：这批样本是对着关键词表写的，天然偏向关键词路由，数字会偏乐观。正式报数前建议：
  1. 找 1 到 2 个没看过代码的人，每人按自己的说话习惯写 20 到 30 条，**并且由他们自己标注**；
  2. 或者从 `chat_messages` 表里抽真实用户提问来人工标注。
- 简历上要报就报这个"盲测集"的准确率，并写明样本量，例如"在 150 条人工标注盲测集上，路由准确率 xx%"。

## 2. 推荐链路：预热缓存 + 并行取数的贡献

```bash
python -m bench.bench_components --runs 10
```

输出四个组合的 p50/p95，其中：

- **优化前** = 冷缓存 + 串行（用户来问时才现拉 akshare，拉完再搜索）
- **优化后** = 热缓存 + 并行（缓存已预热，akshare 和搜索同时跑）

另外两组（冷+并行、热+串行）用来拆分两项优化各自贡献了多少。akshare 受网络影响波动大，建议 `--runs ≥ 10`，并且在不同时段各跑一次。

## 3 & 4. 端到端首字延迟与 Token

```bash
# 终端 1：关掉限流再启动服务（PowerShell 用 $env:RATE_LIMIT_PER_MIN=0 这种写法）
RATE_LIMIT_PER_MIN=0 RATE_LIMIT_PER_DAY=0 uvicorn app.main:app

# 终端 2
python -m bench.bench_latency stream --runs 5 --out bench/results/stream.csv
```

- 在客户端计时，口径和用户体感一致；每个请求用新会话，排除历史长度的干扰；正式计时前先预热一次。
- Token 取自服务端 `meta.tokens_used`，统计的是图内所有 LLM 调用之和（含记忆摘要），**不含**后台被动捕捉财务信息那次调用，报数时要写明口径。
- 真实使用数据也可以直接查库：

```sql
SELECT agent_type, COUNT(*) AS n, AVG(tokens_used) AS avg_tokens, MAX(tokens_used) AS max_tokens
FROM chat_messages WHERE role = 'assistant' AND tokens_used > 0
GROUP BY agent_type;
```

### 做"优化前 vs 优化后"的端到端对比

1. 先用 `--out bench/results/after.csv` 跑当前版本；
2. 用 git 切到优化前的提交（或者临时把 `recommend_node` 里的 `gather` 改回两个串行 `await`，并注释掉 lifespan 里的 `start_live_data_refresher`），重启服务，用 `--out bench/results/before.csv` 再跑一次；
3. 对比两份 CSV 里 recommend 场景的 TTFT p50/p95。

## 5. 缓存

```bash
python -m bench.bench_latency cache --runs 5
```

- **已知问题**：前端走的 `/api/chat/stream` 只**写**缓存、不**读**缓存，只有 `/api/chat/invoke` 会读（见 `app/api/chat.py`）。所以目前真实用户的缓存命中率恒为 0，这个脚本测的是 `/invoke` 路径。
- 没有真实流量时，"命中率"取决于你自己构造的重复比例，**没有意义，不要报**。能报的是"命中时延迟从 X 秒降到 Y 毫秒、省掉 N 个 token"。等有了线上流量，再统计真实命中率。

## 报数规范

- 延迟一律报 p50 和 p95，并注明样本量、模型名（`LLM_MODEL`）和网络环境。
- 结果随模型服务商、网络、akshare 的状态波动，报数时注明测试日期。
- 原始 CSV 保存在 `bench/results/`，面试被追问时可以拿出来。
