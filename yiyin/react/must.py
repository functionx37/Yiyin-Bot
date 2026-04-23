"""
一定要回应（react 子模块）
- 当群友消息中包含「一定要」时
- 从固定表情串中随机抽取 3-7 次并拼成一句话发送
"""

from __future__ import annotations

import random

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.rule import Rule

_PARTS = ("\\😭/", "🤚😭🤚", "✍️😭")


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


def _must_trigger(event: GroupMessageEvent) -> bool:
    """消息中包含「一定要」时触发。"""
    return "一定要" in event.get_plaintext()


must_matcher = on_message(
    Rule(_not_from_bot, _must_trigger),
    priority=64,
    block=False,
)


@must_matcher.handle()
async def handle_must():
    """随机拼接一定要回应。"""
    count = random.randint(3, 7)
    reply = "".join(random.choice(_PARTS) for _ in range(count))
    await must_matcher.finish(reply)
