"""
选择回应（react 子模块）
- 匹配以 # 开头且包含「还是」的多选一结构
- 匹配以 # 开头的「句子1 + 词 + 不 + 相同词 + 句子2」结构
- 支持从 assets/documents/pick_special.json 读取特判问答
- 以最长相同前后缀作为词
- 引用原消息后按概率回复
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SPECIAL_RESPONSES_PATH = PROJECT_ROOT / "assets" / "documents" / "pick_special.json"

_special_responses_cache: dict[str, str] | None = None


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


def _load_special_responses() -> dict[str, str]:
    """加载 pick 特判问答。"""
    global _special_responses_cache
    if _special_responses_cache is not None:
        return _special_responses_cache

    if not SPECIAL_RESPONSES_PATH.exists():
        _special_responses_cache = {}
        return _special_responses_cache

    try:
        with open(SPECIAL_RESPONSES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        _special_responses_cache = {}
        return _special_responses_cache

    responses: dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue

            question = item.get("Q")
            answer = item.get("A")
            if not isinstance(question, str) or not isinstance(answer, str):
                continue

            normalized_question = question.strip()
            normalized_answer = answer.strip()
            if normalized_question and normalized_answer:
                responses[normalized_question] = normalized_answer

    _special_responses_cache = responses
    return _special_responses_cache


def _get_special_reply(text: str) -> str | None:
    """获取 #内容 命中的特判回复。"""
    if not text.startswith("#"):
        return None

    content = text[1:].strip()
    if not content:
        return None

    return _load_special_responses().get(content)


def _extract_alternative_choices(text: str) -> list[str] | None:
    """提取 #选项1还是选项2 结构中的备选项。"""
    if not text.startswith("#"):
        return None

    content = text[1:].strip()
    if "还是" not in content:
        return None

    choices = [part.strip() for part in content.split("还是") if part.strip()]
    return choices if len(choices) >= 2 else None


def _extract_pick_parts(text: str) -> tuple[str, str, str] | None:
    """提取 #句子1词不词句子2 中的三个部分，词取最长匹配。"""
    if not text.startswith("#"):
        return None

    content = text[1:].strip()
    if not content:
        return None

    best_match: tuple[str, str, str] | None = None
    best_len = 0

    for idx, char in enumerate(content):
        if char != "不":
            continue

        left = content[:idx]
        right = content[idx + 1 :]
        max_word_len = min(len(left), len(right))
        for word_len in range(max_word_len, 0, -1):
            word = right[:word_len]
            if left.endswith(word):
                best_match = (left[:-word_len], word, right[word_len:])
                best_len = word_len
                break

        if best_len == max_word_len and best_len > 0:
            break

    return best_match


def _pick_reply(sentence1: str, word: str, sentence2: str) -> str:
    """按概率生成回复文本。"""
    roll = random.random()
    if roll < 0.10:
        return "37不知道哦"
    if roll < 0.55:
        return f"{sentence1}{word}{sentence2}"
    return f"{sentence1}不{word}{sentence2}"


def _build_pick_reply(text: str) -> str | None:
    """根据 pick 规则生成回复文本。"""
    special_reply = _get_special_reply(text)
    if special_reply is not None:
        return special_reply

    choices = _extract_alternative_choices(text)
    if choices is not None:
        return random.choice(choices)

    parts = _extract_pick_parts(text)
    if parts is None:
        return None

    sentence1, word, sentence2 = parts
    return _pick_reply(sentence1, word, sentence2)


def _pick_trigger(event: GroupMessageEvent) -> bool:
    """只在消息成功匹配 pick 结构时触发。"""
    return _build_pick_reply(event.get_plaintext().strip()) is not None


pick_matcher = on_message(
    Rule(_not_from_bot, _pick_trigger),
    priority=62,
    block=False,
)


@pick_matcher.handle()
async def handle_pick(event: GroupMessageEvent):
    """引用原消息并发送 pick 结果。"""
    reply_text = _build_pick_reply(event.get_plaintext().strip())
    if reply_text is None:
        return

    reply = MessageSegment.reply(event.message_id) + Message(reply_text)
    await pick_matcher.finish(reply)
