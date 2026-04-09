"""
聚餐接龙：触发词见 assets/documents/chain/dinner.json。
午餐 10:00–14:00、晚餐 16:00–20:00（本地时区，左闭右开）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Literal

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.internal.adapter import Event
from nonebot.params import CommandArg
from nonebot.rule import Rule

from yiyin.chain.common import (
    DINNER_DATA_DIR,
    display_name,
    format_chain_message,
    group_json_lock,
    read_json,
    write_json_atomic,
)

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "documents" / "chain" / "dinner.json"
)
with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _CFG: dict[str, Any] = json.load(_f)
TRIGGERS: tuple[str, ...] = tuple(_CFG.get("triggers", []))

_EMPTY: dict[str, Any] = {"period_key": "", "participants": [], "venue": ""}

Slot = Literal["lunch", "dinner"]


def _current_dinner_period(now: datetime | None = None) -> tuple[str, Slot] | None:
    now = (now or datetime.now()).astimezone()
    d = now.date().isoformat()
    t = now.time()
    if time(10, 0) <= t < time(14, 0):
        return f"{d}-lunch", "lunch"
    if time(16, 0) <= t < time(20, 0):
        return f"{d}-dinner", "dinner"
    return None


def _header_for_slot(slot: Slot, venue: str) -> str:
    v = venue.strip()
    if v:
        return f"{v}聚餐："
    if slot == "lunch":
        return "午餐聚餐："
    return "晚餐聚餐："


def _join_dinner_sync(
    group_id: int, user_id: str, nick: str
) -> tuple[bool, str | None]:
    period_slot = _current_dinner_period()
    if period_slot is None:
        return False, None
    period_key, slot = period_slot

    with group_json_lock(DINNER_DATA_DIR, group_id) as data_path:
        state = read_json(data_path, _EMPTY)
        if state.get("period_key") != period_key:
            state = {"period_key": period_key, "participants": [], "venue": ""}

        uid = str(user_id)
        participants: list[dict[str, str]] = list(state.get("participants", []))
        for p in participants:
            if p.get("user_id") == uid:
                return False, None

        participants.append({"user_id": uid, "nickname": nick})
        venue = str(state.get("venue") or "")
        state["period_key"] = period_key
        state["participants"] = participants
        write_json_atomic(data_path, state)
        header = _header_for_slot(slot, venue)
        return True, format_chain_message(header, participants)


def _leave_dinner_sync(group_id: int, user_id: str) -> str | None:
    period_slot = _current_dinner_period()
    if period_slot is None:
        return None
    period_key, slot = period_slot

    with group_json_lock(DINNER_DATA_DIR, group_id) as data_path:
        state = read_json(data_path, _EMPTY)
        if state.get("period_key") != period_key:
            return "你不在当前聚餐接龙中。"

        uid = str(user_id)
        participants: list[dict[str, str]] = list(state.get("participants", []))
        new_list = [p for p in participants if p.get("user_id") != uid]
        if len(new_list) == len(participants):
            return "你不在当前聚餐接龙中。"

        venue = str(state.get("venue") or "")
        state["participants"] = new_list
        write_json_atomic(data_path, state)
        header = _header_for_slot(slot, venue)
        return format_chain_message(header, new_list)


def _set_venue_sync(group_id: int, venue: str) -> tuple[bool, str]:
    period_slot = _current_dinner_period()
    if period_slot is None:
        return False, "当前不在聚餐接龙时段。"
    period_key, slot = period_slot

    with group_json_lock(DINNER_DATA_DIR, group_id) as data_path:
        state = read_json(data_path, _EMPTY)
        if state.get("period_key") != period_key:
            state = {"period_key": period_key, "participants": [], "venue": venue}
        else:
            state["venue"] = venue
        write_json_atomic(data_path, state)
        header = _header_for_slot(slot, venue)
        return True, format_chain_message(header, state.get("participants", []))


def _dinner_trigger(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.self_id == event.user_id:
        return False
    if not TRIGGERS:
        return False
    if _current_dinner_period() is None:
        return False
    text = event.get_plaintext()
    return any(t in text for t in TRIGGERS)


dinner_matcher = on_message(rule=Rule(_dinner_trigger), priority=12, block=True)


@dinner_matcher.handle()
async def handle_dinner(_bot: Bot, event: GroupMessageEvent):
    name = display_name(event)
    _added, msg = await asyncio.to_thread(
        _join_dinner_sync,
        event.group_id,
        str(event.user_id),
        name,
    )
    if msg is not None:
        await dinner_matcher.send(msg)


leave_dinner_cmd = on_command("我不饭了", priority=10, block=True)


@leave_dinner_cmd.handle()
async def handle_leave_dinner(_bot: Bot, event: GroupMessageEvent):
    if _current_dinner_period() is None:
        return
    msg = await asyncio.to_thread(
        _leave_dinner_sync, event.group_id, str(event.user_id)
    )
    if msg is not None:
        await leave_dinner_cmd.finish(msg)


eat_cmd = on_command("去吃", priority=10, block=True)


@eat_cmd.handle()
async def handle_eat(_bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if _current_dinner_period() is None:
        return
    raw = args.extract_plain_text().strip()
    if not raw:
        await eat_cmd.finish("用法：/去吃 <店名>")
    ok, body = await asyncio.to_thread(_set_venue_sync, event.group_id, raw)
    if ok:
        await eat_cmd.finish(body)
