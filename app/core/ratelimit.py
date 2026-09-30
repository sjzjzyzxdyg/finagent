"""按用户限流 + 每日配额（保护大模型额度，防止被刷爆）。

单实例内存实现：每分钟用滑动窗口、每日用计数。多实例部署时应换成 Redis 计数。
settings.rate_limit_per_min / per_day 为 0 时对应维度不限。
"""
from __future__ import annotations

import datetime
import time
from collections import defaultdict, deque

from app.config import settings

_minute: dict[str, deque] = defaultdict(deque)   # user -> 最近 60s 内的请求时间戳
_day: dict[str, tuple[str, int]] = {}            # user -> (yyyy-mm-dd, 当日计数)


def check_and_consume(user: str) -> tuple[bool, str]:
    """检查并占用一次配额。返回 (是否放行, 拒绝原因文案)。"""
    now = time.time()

    pm = settings.rate_limit_per_min
    if pm > 0:
        q = _minute[user]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= pm:
            return False, f"请求过于频繁（每分钟上限 {pm} 次），请稍等一会儿再问。"

    pd = settings.rate_limit_per_day
    if pd > 0:
        today = datetime.date.today().isoformat()
        d, c = _day.get(user, (today, 0))
        if d != today:
            d, c = today, 0
        if c >= pd:
            return False, f"今日提问次数已达上限（{pd} 次），明天再来吧。"
        _day[user] = (d, c + 1)

    if pm > 0:
        _minute[user].append(now)
    return True, ""
