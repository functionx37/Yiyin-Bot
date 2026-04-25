"""
重复复读功能（react 子模块，隐藏功能，需 /启用 复读）
- 若群友连续发送两条完全相同的可复读消息，机器人复读一次
- 仅在成功触发复读后写入 data/react/repetition.json
- 若本次预备触发的签名与上次成功触发相同，则不再触发
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.toggle import is_feature_enabled

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "react" / "repetition.json"

_FEATURE_KEY = "yiyin.react.repetition"
_candidate_cache: dict[str, dict[str, Any]] = {}


def _load_state() -> dict:
    """从文件读取复读触发记录。"""
    if not STATE_PATH.exists():
        return {"groups": {}}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"groups": {}}

    if not isinstance(data, dict):
        return {"groups": {}}
    groups = data.get("groups")
    if not isinstance(groups, dict):
        data["groups"] = {}
    return data


def _save_state(state: dict) -> None:
    """将复读触发记录写回文件。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _group_candidate(group_id: str) -> dict[str, Any]:
    """获取群内当前连续消息候选，仅保存在内存中。"""
    return _candidate_cache.setdefault(
        group_id,
        {
            "last_signature": None,
            "last_count": 0,
            "last_uniform_reply_id": None,
        },
    )


def _build_repeat_payload(message: Message) -> tuple[Message | None, str | None]:
    """提取可安全复读的消息，并同步生成比较签名。"""
    repeat_message = Message()
    parts: list[list[str]] = []
    for segment in message:
        if segment.type == "text":
            text = segment.data.get("text", "")
            repeat_message.append(MessageSegment.text(text))
            parts.append(["text", text])
            continue
        if segment.type == "face":
            face_id = str(segment.data.get("id", ""))
            repeat_message.append(MessageSegment.face(face_id))
            parts.append(["face", face_id])
            continue
        if segment.type == "at":
            target = str(segment.data.get("qq", ""))
            repeat_message.append(MessageSegment.at(target))
            parts.append(["at", target])
            continue
        return None, None

    if not parts:
        return None, None
    signature = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return repeat_message, signature


def _update_repeat_candidate(
    candidate: dict[str, Any], signature: str | None, reply_message_id: int | None
) -> None:
    """更新当前连续消息候选。"""
    if signature is None:
        candidate["last_signature"] = None
        candidate["last_count"] = 0
        candidate["last_uniform_reply_id"] = None
        return

    if candidate.get("last_signature") == signature:
        candidate["last_count"] = int(candidate.get("last_count", 0)) + 1
        if candidate.get("last_uniform_reply_id") != reply_message_id:
            candidate["last_uniform_reply_id"] = None
        return

    candidate["last_signature"] = signature
    candidate["last_count"] = 1
    candidate["last_uniform_reply_id"] = reply_message_id


def _should_repeat_now(candidate: dict[str, Any], signature: str | None) -> bool:
    """仅在第二条连续相同消息到来时进入预备触发。"""
    if signature is None:
        return False
    return int(candidate.get("last_count", 0)) == 2


def _last_trigger_signature(group_id: str) -> str | None:
    """读取本群上次成功触发的复读签名。"""
    state = _load_state()
    groups = state.get("groups", {})
    if not isinstance(groups, dict):
        return None
    group_state = groups.get(group_id, {})
    if not isinstance(group_state, dict):
        return None
    value = group_state.get("last_trigger_signature")
    return value if isinstance(value, str) else None


def _record_trigger_signature(group_id: str, signature: str) -> None:
    """在成功触发复读后写入本群最近一次触发签名。"""
    state = _load_state()
    groups = state.setdefault("groups", {})
    if not isinstance(groups, dict):
        groups = {}
        state["groups"] = groups
    group_state = groups.setdefault(group_id, {})
    if not isinstance(group_state, dict):
        group_state = {}
        groups[group_id] = group_state
    group_state["last_trigger_signature"] = signature
    _save_state(state)


def _should_send_empty_reply_easter_egg(
    repeat_message: Message | None, uniform_reply_id: int | None
) -> bool:
    """同引用、同单个 at 且无正文时，改发空串彩蛋。"""
    if repeat_message is None or uniform_reply_id is None:
        return False

    at_targets: list[str] = []
    for segment in repeat_message:
        if segment.type == "at":
            at_targets.append(str(segment.data.get("qq", "")))
            continue
        if segment.type == "text" and not segment.data.get("text", "").strip():
            continue
        return False

    return len(at_targets) == 1


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


async def _repetition_enabled(event: GroupMessageEvent) -> bool:
    """仅在当前群启用了复读功能时触发。"""
    return is_feature_enabled(_FEATURE_KEY, str(event.group_id))


repetition_matcher = on_message(
    Rule(_not_from_bot, _repetition_enabled),
    priority=60,
    block=False,
)


@repetition_matcher.handle()
async def handle_repetition(bot: Bot, event: GroupMessageEvent):
    """处理连续相同消息的复读。"""
    group_id = str(event.group_id)
    candidate = _group_candidate(group_id)

    repeat_message, signature = _build_repeat_payload(event.message)
    reply_message_id = event.reply.message_id if event.reply else None
    _update_repeat_candidate(candidate, signature, reply_message_id)

    if not _should_repeat_now(candidate, signature):
        return
    if signature == _last_trigger_signature(group_id):
        return

    uniform_reply_id = candidate.get("last_uniform_reply_id")
    if _should_send_empty_reply_easter_egg(repeat_message, uniform_reply_id):
        outgoing = MessageSegment.reply(uniform_reply_id) + MessageSegment.text("")
    else:
        outgoing = repeat_message
        if uniform_reply_id is not None:
            outgoing = MessageSegment.reply(uniform_reply_id) + outgoing

    setattr(event, "_yiyin_repetition_triggered", True)
    await bot.send(event, outgoing)
    assert signature is not None
    _record_trigger_signature(group_id, signature)
