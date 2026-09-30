"""全局配置：基于 pydantic-settings，从环境变量 / .env 读取。

设计原则：所有外部依赖都可缺省。缺省时项目进入 mock 模式
（内存 LLM、SQLite、内存向量库、内存 checkpointer、内置知识库播种），实现零配置启动。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 应用信息 ----
    app_name: str = "小白理财智能助手"
    app_desc: str = "面向理财小白的一体化平台：学知识 · 看产品 · 测风险 · 挑产品"

    # ---- LLM (DeepSeek / Qwen / OpenAI 兼容协议) ----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3
    llm_timeout: int = 60          # 单次请求超时（秒）
    llm_max_retries: int = 2       # ChatOpenAI 内建重试次数
    llm_rate_per_sec: int = 8      # 令牌桶：每秒最大 LLM 调用数

    # ---- DB ----
    mysql_dsn: str = ""

    # ---- Redis ----
    redis_url: str = ""

    # ---- 联网取真实数据（可选，默认关闭；打开后 product/recommend 优先联网取数） ----
    enable_live_data: bool = False        # True 时 agent 先用 akshare 公开数据，失败再联网搜索
    live_data_refresh_sec: int = 600      # 后台每隔多少秒预热刷新一次基金排行缓存（让用户查询直接命中）
    # 运行时是否允许回退到内置示例产品库：默认 False——拿不到真实数据就如实说"查不到"，不给演示数据。
    # （内置示例库仅保留给测试直接调用；把它设 True 可恢复"真实数据取不到时用示例兜底"的旧行为。）
    sample_fallback: bool = False
    web_search_api_key: str = ""          # 方法2 联网搜索的 key（如 Tavily）；空则跳过方法2
    web_search_url: str = "https://api.tavily.com/search"

    # ---- Auth ----
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    auth_enabled: bool = False   # True 时 /api/chat 等受保护接口需 Bearer token

    # ---- 限流 / 成本控制（保护大模型额度；0 表示不限）----
    rate_limit_per_min: int = 15    # 每用户每分钟提问上限
    rate_limit_per_day: int = 300   # 每用户每日提问上限

    # ---- 响应速度 ----
    # 关键词判不出意图的模糊问题：默认(False)直接走知识问答(education，可立即流式、且是友好兜底)，
    # 省掉一次分类大模型往返，缩短"思考时间"。设 True 则改用大模型精确分类（更准但更慢）。
    router_llm_fallback: bool = False
    react_max_steps: int = 3        # 产品/研究类 ReAct 的最大工具调用轮次（越小越快、信息可能略少）

    # ---- Service ----
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # ---- 派生标志：是否处于 mock / 降级模式 ----
    @property
    def insecure_jwt_secret(self) -> bool:
        """JWT 密钥是否仍是占位/弱值（上线且开启鉴权时必须替换为强随机值）。"""
        weak = {"", "change-me-in-production", "change-me-please-use-a-long-random-string"}
        return self.jwt_secret.strip() in weak or len(self.jwt_secret.strip()) < 16

    @property
    def llm_mock_mode(self) -> bool:
        return not self.openai_api_key

    @property
    def db_url(self) -> str:
        # 缺省用 SQLite，保证零配置可启动
        return self.mysql_dsn or "sqlite+aiosqlite:///./data/pyfinagent.db"

    @property
    def use_redis(self) -> bool:
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
