"""
Yiyin-Bot 网页展示链接插件
- 命令：/web
- 功能：生成当日有效的群聊数据访问链接
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent


def _timezone_name() -> str:
    return os.getenv("TOKEN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"


def _current_time() -> datetime:
    return datetime.now(ZoneInfo(_timezone_name()))


def _build_token(group_id: str, now: datetime | None = None) -> str:
    secret = os.getenv("WEB_TOKEN_SECRET", "").strip()
    if not secret:
        raise ValueError("未配置 WEB_TOKEN_SECRET，无法生成网页链接。")
    current = now.astimezone(ZoneInfo(_timezone_name())) if now else _current_time()
    payload = f"{group_id}:{current.strftime('%Y-%m-%d')}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _expires_at(now: datetime | None = None) -> datetime:
    current = now.astimezone(ZoneInfo(_timezone_name())) if now else _current_time()
    next_day = current + timedelta(days=1)
    return current.replace(
        year=next_day.year,
        month=next_day.month,
        day=next_day.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


web_cmd = on_command("web", priority=10, block=True)


@web_cmd.handle()
async def handle_web(event: GroupMessageEvent):
    site_base_url = os.getenv("SITE_BASE_URL", "").strip().rstrip("/")
    if not site_base_url:
        await web_cmd.finish("未配置 SITE_BASE_URL，无法生成网页链接。")

    try:
        token = _build_token(str(event.group_id))
    except ValueError as exc:
        await web_cmd.finish(str(exc))

    expires_at = _expires_at().strftime("%Y-%m-%d %H:%M:%S")
    url = f"{site_base_url}/yiyin/{event.group_id}?token={token}"
    await web_cmd.finish(
        f"群网页链接：\n{url}\n\n该链接将于 {expires_at}（{_timezone_name()}）失效。"
    )
