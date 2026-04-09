"""
NoneBot2 KTV 接龙插件
- 群内消息包含配置中的触发词（如「有k吗」）时记录展示名并回复接龙列表
- 展示名：QQ 昵称优先，若无则用群昵称（card），再没有则用 QQ 号
- 周期为当日 6:00 至次日 6:00；配置见 assets/documents/ktv.json
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.internal.adapter import Event
from nonebot.rule import Rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KTV_DATA_DIR = PROJECT_ROOT / "data" / "ktv"
KTV_CONFIG_PATH = PROJECT_ROOT / "assets" / "documents" / "ktv.json"

with open(KTV_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _KTV_CFG: dict[str, Any] = json.load(_f)
TRIGGERS: tuple[str, ...] = tuple(_KTV_CFG.get("triggers", []))


def _current_period_key(now: datetime | None = None) -> str:
    """当前接龙周期起始日的 YYYY-MM-DD（周期为 6:00 至次日 6:00）。"""
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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"period_key": "", "participants": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"period_key": "", "participants": []}
        data.setdefault("period_key", "")
        data.setdefault("participants", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"period_key": "", "participants": []}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _display_name(event: GroupMessageEvent) -> str:
    sender = event.sender
    if not sender:
        return str(event.user_id)
    nick = (sender.nickname or "").strip()
    if nick:
        return nick
    card = (sender.card or "").strip()
    if card:
        return card
    return str(event.user_id)


def _join_ktv_sync(group_id: int, user_id: str, display_name: str) -> tuple[bool, str | None]:
    """
    在文件锁内更新接龙数据。返回 (是否新加入, 要发送的文本)；重复参与时第二项为 None（不回复）。
    """
    KTV_DATA_DIR.mkdir(parents=True, exist_ok=True)
    data_path = KTV_DATA_DIR / f"{group_id}.json"
    lock_path = KTV_DATA_DIR / f"{group_id}.json.lock"

    with open(lock_path, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            period = _current_period_key()
            state = _read_json(data_path)
            if state.get("period_key") != period:
                state = {"period_key": period, "participants": []}

            uid = str(user_id)
            participants: list[dict[str, str]] = list(state.get("participants", []))
            for p in participants:
                if p.get("user_id") == uid:
                    return False, None

            participants.append({"user_id": uid, "nickname": display_name})
            state["period_key"] = period
            state["participants"] = participants
            _write_json_atomic(data_path, state)

            label = _period_label_zh(period)
            lines = [f"{label}KTV接龙："]
            for i, p in enumerate(participants, 1):
                lines.append(f"{i}. {p['nickname']}")
            return True, "\n".join(lines)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


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
    name = _display_name(event)
    _added, msg = await asyncio.to_thread(
        _join_ktv_sync,
        event.group_id,
        str(event.user_id),
        name,
    )
    if msg is not None:
        await ktv_matcher.send(msg)
