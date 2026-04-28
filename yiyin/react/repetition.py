"""
重复复读功能（react 子模块，隐藏功能，需 /启用 复读）
- 若群友连续发送两条除引用外完全相同的可复读消息，机器人复读一次
- 仅在成功触发复读后写入 data/react/repetition.json
- 数据以结构化段列表保存，显式记录 cite / text / emoji / at
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.toggle import is_feature_enabled_async

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "react" / "repetition.json"

_FEATURE_KEY = "yiyin.react.repetition"
_candidate_cache: dict[str, dict[str, Any]] = {}
_SIGNATURE_TYPES = {"cite", "text", "emoji", "at"}


def _load_state() -> dict:
    """从文件读取复读触发记录。"""
    if not STATE_PATH.exists():
        return {}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
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


def _make_segment(seg_type: str, content: str | int) -> dict[str, str | int]:
    """构造标准化消息段。"""
    return {"type": seg_type, "content": content}


def _build_body_signature(
    message: Message, self_id: str | int
) -> list[dict[str, str | int]] | None:
    """提取用于触发复读的正文签名，自动去除 @机器人。"""
    parts: list[dict[str, str | int]] = []
    for segment in message:
        if segment.type == "text":
            text = segment.data.get("text", "")
            parts.append(_make_segment("text", text))
            continue
        if segment.type == "face":
            face_id = str(segment.data.get("id", ""))
            parts.append(_make_segment("emoji", face_id))
            continue
        if segment.type == "at":
            target = str(segment.data.get("qq", ""))
            if target == str(self_id):
                continue
            parts.append(_make_segment("at", target))
            continue
        return None
    return parts


def _update_repeat_candidate(
    candidate: dict[str, Any],
    signature: list[dict[str, str | int]] | None,
    reply_message_id: int | None,
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


def _should_repeat_now(
    candidate: dict[str, Any], signature: list[dict[str, str | int]] | None
) -> bool:
    """仅在第二条连续相同消息到来时进入预备触发。"""
    if signature is None:
        return False
    return int(candidate.get("last_count", 0)) == 2


def _normalize_signature(
    raw: Any,
) -> list[dict[str, str | int]] | None:
    """校验并标准化从文件读取的签名结构。"""
    if not isinstance(raw, list):
        return None

    normalized: list[dict[str, str | int]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        seg_type = item.get("type")
        if not isinstance(seg_type, str) or seg_type not in _SIGNATURE_TYPES:
            return None
        if "content" not in item:
            return None
        content = item["content"]
        if seg_type == "cite":
            if not isinstance(content, (str, int)):
                return None
            normalized.append(_make_segment("cite", content))
            continue
        if not isinstance(content, str):
            return None
        normalized.append(_make_segment(seg_type, content))
    return normalized


def _last_trigger_signature(group_id: str) -> list[dict[str, str | int]] | None:
    """读取本群上次成功发送的结构化签名。"""
    state = _load_state()
    return _normalize_signature(state.get(group_id))


def _record_trigger_signature(
    group_id: str, signature: list[dict[str, str | int]]
) -> None:
    """在成功触发复读后写入本群最近一次发送签名。"""
    state = _load_state()
    state[group_id] = signature
    _save_state(state)


def _strip_cite(
    signature: list[dict[str, str | int]],
) -> list[dict[str, str | int]]:
    """移除 cite 段，仅保留正文签名。"""
    return [seg for seg in signature if seg["type"] != "cite"]


def _build_prepublish_signature(
    body_signature: list[dict[str, str | int]], uniform_reply_id: int | None
) -> list[dict[str, str | int]]:
    """生成预发布消息的结构化签名。"""
    signature: list[dict[str, str | int]] = []
    if uniform_reply_id is not None:
        signature.append(_make_segment("cite", uniform_reply_id))
    signature.extend(body_signature)
    return signature


def _body_is_empty(body_signature: list[dict[str, str | int]]) -> bool:
    """判断正文是否完全为空。"""
    for seg in body_signature:
        if seg["type"] == "text":
            if seg["content"] != "":
                return False
            continue
        return False
    return True


def _body_has_only_at(body_signature: list[dict[str, str | int]]) -> bool:
    """判断正文是否仅包含 at（允许夹杂空字符串 text）。"""
    saw_at = False
    for seg in body_signature:
        if seg["type"] == "at":
            saw_at = True
            continue
        if seg["type"] == "text" and seg["content"] == "":
            continue
        return False
    return saw_at


def _finalize_outgoing_signature(
    prepublish_signature: list[dict[str, str | int]],
) -> list[dict[str, str | int]] | None:
    """按发送规则裁剪预发布签名。"""
    has_cite = any(seg["type"] == "cite" for seg in prepublish_signature)
    body_signature = _strip_cite(prepublish_signature)

    if _body_is_empty(body_signature):
        if not has_cite:
            return None
        return [seg for seg in prepublish_signature if seg["type"] == "cite"]

    if has_cite and _body_has_only_at(body_signature):
        return [seg for seg in prepublish_signature if seg["type"] == "cite"]

    return prepublish_signature


def _build_message_from_signature(
    signature: list[dict[str, str | int]]
) -> Message:
    """将结构化签名还原为 OneBot 消息。"""
    message = Message()
    has_non_cite_segment = False
    for seg in signature:
        seg_type = seg["type"]
        content = seg["content"]
        if seg_type == "cite":
            message.append(MessageSegment.reply(content))
            continue
        has_non_cite_segment = True
        if seg_type == "text":
            message.append(MessageSegment.text(str(content)))
            continue
        if seg_type == "emoji":
            message.append(MessageSegment.face(str(content)))
            continue
        if seg_type == "at":
            message.append(MessageSegment.at(str(content)))

    if not has_non_cite_segment:
        message.append(MessageSegment.text(""))
    return message


def _same_body_as_last_trigger(
    group_id: str, body_signature: list[dict[str, str | int]]
) -> bool:
    """检查正文是否与上次成功发送内容相同。"""
    last_signature = _last_trigger_signature(group_id)
    if last_signature is None:
        return False
    return _strip_cite(last_signature) == body_signature


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


async def _repetition_enabled(bot: Bot, event: GroupMessageEvent) -> bool:
    """仅在当前群启用了复读功能时触发。"""
    return await is_feature_enabled_async(bot, _FEATURE_KEY, str(event.group_id))


async def _should_handle_repetition(event: GroupMessageEvent) -> bool:
    """仅在真正需要触发复读时才进入 matcher。"""
    if not _not_from_bot(event):
        return False
    if not await _repetition_enabled(event):
        return False

    group_id = str(event.group_id)
    candidate = _group_candidate(group_id)
    signature = _build_body_signature(event.message, event.self_id)
    reply_message_id = event.reply.message_id if event.reply else None
    _update_repeat_candidate(candidate, signature, reply_message_id)

    if not _should_repeat_now(candidate, signature):
        return False
    assert signature is not None
    if _same_body_as_last_trigger(group_id, signature):
        return False

    prepublish_signature = _build_prepublish_signature(
        signature, candidate.get("last_uniform_reply_id")
    )
    outgoing_signature = _finalize_outgoing_signature(prepublish_signature)
    if outgoing_signature is None:
        return False

    setattr(event, "_yiyin_repetition_outgoing_signature", outgoing_signature)
    return True


repetition_matcher = on_message(
    Rule(_should_handle_repetition),
    priority=60,
    block=False,
)


@repetition_matcher.handle()
async def handle_repetition(bot: Bot, event: GroupMessageEvent):
    """处理连续相同消息的复读。"""
    group_id = str(event.group_id)
    outgoing_signature = getattr(event, "_yiyin_repetition_outgoing_signature", None)
    if outgoing_signature is None:
        return
    setattr(event, "_yiyin_repetition_triggered", True)
    await bot.send(event, _build_message_from_signature(outgoing_signature))
    _record_trigger_signature(group_id, outgoing_signature)
