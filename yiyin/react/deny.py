"""
否定回应（react 子模块，隐藏功能，需 /启用 否定）
- 当群友消息以 但/但是 开头时，有 50% 概率回复：
  我们不认为<句子>，您囍疯。
- 默认关闭，需群内 /启用 否定 后生效
"""

import random

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from yiyin.toggle import is_feature_enabled_async

_FEATURE_KEY = "yiyin.react.deny"
_REPLY_PROBABILITY = 0.5
_PREFIXES = ("但是", "但")


def _extract_sentence(text: str) -> str | None:
    """提取 但/但是 后面的句子，空内容返回 None。"""
    normalized = text.strip()
    for prefix in _PREFIXES:
        if normalized.startswith(prefix):
            sentence = normalized[len(prefix) :].strip()
            if sentence.startswith(("，", ",")):
                sentence = sentence[1:].strip()
            return sentence or None
    return None


def _swap_person_pronouns(text: str) -> str:
    """将常见第一/第二人称互换，避免原句直接照搬。"""
    placeholders = {
        "你们": "__P2_PLURAL__",
        "我们": "__P1_PLURAL__",
        "你的": "__P2_POSSESSIVE__",
        "我的": "__P1_POSSESSIVE__",
        "您的": "__P2_POLITE_POSSESSIVE__",
        "咱们": "__P1_INCLUSIVE_PLURAL__",
        "咱": "__P1_INCLUSIVE__",
        "你": "__P2__",
        "我": "__P1__",
        "您": "__P2_POLITE__",
    }
    swapped = text
    for source, placeholder in placeholders.items():
        swapped = swapped.replace(source, placeholder)

    return (
        swapped.replace("__P2_PLURAL__", "我们")
        .replace("__P1_PLURAL__", "你们")
        .replace("__P2_POSSESSIVE__", "我的")
        .replace("__P1_POSSESSIVE__", "你的")
        .replace("__P2_POLITE_POSSESSIVE__", "我的")
        .replace("__P1_INCLUSIVE_PLURAL__", "你们")
        .replace("__P1_INCLUSIVE__", "你")
        .replace("__P2__", "我")
        .replace("__P1__", "你")
        .replace("__P2_POLITE__", "我")
    )


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己发送的消息。"""
    return str(event.self_id) != str(event.user_id)


async def _deny_enabled(bot: Bot, event: GroupMessageEvent) -> bool:
    """仅在当前群启用了否定功能时触发。"""
    return await is_feature_enabled_async(bot, _FEATURE_KEY, str(event.group_id))


def _deny_trigger(event: GroupMessageEvent) -> bool:
    """消息以 但/但是 开头且后面有正文。"""
    return _extract_sentence(event.get_plaintext()) is not None


deny_matcher = on_message(
    Rule(_not_from_bot, _deny_enabled, _deny_trigger),
    priority=60,
    block=False,
)


@deny_matcher.handle()
async def handle_deny(event: GroupMessageEvent):
    """按概率发送否定回应。"""
    if random.random() >= _REPLY_PROBABILITY:
        return

    sentence = _extract_sentence(event.get_plaintext())
    if sentence is None:
        return

    swapped_sentence = _swap_person_pronouns(sentence)
    await deny_matcher.finish(f"我们不认为{swapped_sentence}，您囍疯。")
