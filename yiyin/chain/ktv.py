"""
KTV 接龙：触发词见 assets/documents/chain/ktv.json；周期 6:00–次日 6:00。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.internal.adapter import Event
from nonebot.rule import Rule

from yiyin.chain.common import (
    KTV_DATA_DIR,
    display_name,
    format_chain_message,
    group_json_lock,
    read_json,
    write_json_atomic,
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "documents" / "chain" / "ktv.json"
with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _CFG: dict[str, Any] = json.load(_f)
TRIGGERS: tuple[str, ...] = tuple(_CFG.get("triggers", []))

_EMPTY: dict[str, Any] = {"period_key": "", "participants": []}


def _current_period_key(now: datetime | None = None) -> str:
    now = (now or datetime.now()).astimezone()
    today_6 = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < today_6:
        start = (now - timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
    else:
        start = today_6
    return start.strftime("%Y-%m-%d")


def _period_label_zh(period_key: str) -> str:
    _y, m, d = period_key.split("-")
    return f"{int(m)}月{int(d)}日"


def _ktv_header(period_key: str) -> str:
    return f"{_period_label_zh(period_key)}KTV接龙："


def _join_ktv_sync(group_id: int, user_id: str, nick: str) -> tuple[bool, str | None]:
    with group_json_lock(KTV_DATA_DIR, group_id) as data_path:
        period = _current_period_key()
        state = read_json(data_path, _EMPTY)
        if state.get("period_key") != period:
            state = {"period_key": period, "participants": []}

        uid = str(user_id)
        participants: list[dict[str, str]] = list(state.get("participants", []))
        for p in participants:
            if p.get("user_id") == uid:
                return False, None

        participants.append({"user_id": uid, "nickname": nick})
        state["period_key"] = period
        state["participants"] = participants
        write_json_atomic(data_path, state)
        header = _ktv_header(period)
        return True, format_chain_message(header, participants)


def _leave_ktv_sync(group_id: int, user_id: str) -> str:
    with group_json_lock(KTV_DATA_DIR, group_id) as data_path:
        period = _current_period_key()
        state = read_json(data_path, _EMPTY)
        if state.get("period_key") != period:
            return "你不在当前 KTV 接龙中。"

        uid = str(user_id)
        participants: list[dict[str, str]] = list(state.get("participants", []))
        new_list = [p for p in participants if p.get("user_id") != uid]
        if len(new_list) == len(participants):
            return "你不在当前 KTV 接龙中。"

        state["participants"] = new_list
        write_json_atomic(data_path, state)
        header = _ktv_header(period)
        return format_chain_message(header, new_list)


def _ktv_trigger(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.self_id == event.user_id:
        return False
    if not TRIGGERS:
        return False
    text = event.get_plaintext()
    return any(t in text for t in TRIGGERS)


ktv_matcher = on_message(rule=Rule(_ktv_trigger), priority=12, block=True)


@ktv_matcher.handle()
async def handle_ktv(_bot: Bot, event: GroupMessageEvent):
    name = display_name(event)
    _added, msg = await asyncio.to_thread(
        _join_ktv_sync,
        event.group_id,
        str(event.user_id),
        name,
    )
    if msg is not None:
        await ktv_matcher.send(msg)


leave_ktv_cmd = on_command("我不k了", priority=10, block=True)


@leave_ktv_cmd.handle()
async def handle_leave_ktv(_bot: Bot, event: GroupMessageEvent):
    msg = await asyncio.to_thread(
        _leave_ktv_sync, event.group_id, str(event.user_id)
    )
    await leave_ktv_cmd.finish(msg)
