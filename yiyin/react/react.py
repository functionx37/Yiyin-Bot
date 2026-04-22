"""
随机回应（react 子模块）
- 当群友发送以 # 开头且未命中 pick 的文本时
- 引用原消息，并从 assets/documents/react.json 随机抽一条回复
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.react.pick import _extract_pick_parts

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESPONSES_PATH = PROJECT_ROOT / "assets" / "documents" / "react.json"

_responses_cache: list[str] | None = None


def _load_responses() -> list[str]:
    """加载随机回应文案。"""
    global _responses_cache
    if _responses_cache is not None:
        return _responses_cache

    with open(RESPONSES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    _responses_cache = [item for item in data if isinstance(item, str) and item.strip()]
    return _responses_cache


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


def _react_trigger(event: GroupMessageEvent) -> bool:
    """匹配 # 开头但不能触发 pick 的文本消息。"""
    text = event.get_plaintext().strip()
    return text.startswith("#") and len(text) > 1 and _extract_pick_parts(text) is None


react_matcher = on_message(
    Rule(_not_from_bot, _react_trigger),
    priority=63,
    block=False,
)


@react_matcher.handle()
async def handle_react(event: GroupMessageEvent):
    """引用原消息并随机发送一条回应。"""
    responses = _load_responses()
    if not responses:
        return

    reply = MessageSegment.reply(event.message_id) + Message(random.choice(responses))
    await react_matcher.finish(reply)
