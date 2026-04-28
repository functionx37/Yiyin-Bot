"""
乱序复读功能（react 子模块，隐藏功能，需 /启用 乱序复读）
- 对可处理文本维护独立渐进概率
- 状态单独保存在 data/react/random.json
- 与重复复读分离；若当前消息已触发重复复读，则本次不再乱序复读
"""

from __future__ import annotations

import json
import random as random_lib
from pathlib import Path

import jieba
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from yiyin.toggle import is_feature_enabled_async

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "react" / "random.json"

_FEATURE_KEY = "yiyin.react.random"
_RANDOM_BASE_PROBABILITY = 0.00015
_RANDOM_INCREMENT = 0.00015
_RANDOM_MAX_PROBABILITY = 0.3


def _load_state() -> dict:
    """从文件读取乱序复读状态。"""
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
    """将乱序复读状态写回文件。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _group_state(state: dict, group_id: str) -> dict:
    """获取单个群的乱序复读状态。"""
    groups = state.setdefault("groups", {})
    if not isinstance(groups, dict):
        groups = {}
        state["groups"] = groups
    group_state = groups.setdefault(group_id, {})
    if not isinstance(group_state, dict):
        group_state = {}
        groups[group_id] = group_state
    group_state.setdefault("random_failures", 0)
    return group_state


def _should_trigger_shuffle(group_state: dict) -> bool:
    """按渐进概率判断是否触发乱序复读。"""
    failures = int(group_state.get("random_failures", 0))
    probability = min(
        _RANDOM_BASE_PROBABILITY + failures * _RANDOM_INCREMENT,
        _RANDOM_MAX_PROBABILITY,
    )
    if random_lib.random() < probability:
        group_state["random_failures"] = 0
        return True

    group_state["random_failures"] = failures + 1
    return False


def _build_shuffled_reply(text: str) -> str | None:
    """对文本分词后打乱顺序，尽量避免和原文相同。"""
    normalized = text.strip()
    if not normalized:
        return None

    words = [word for word in jieba.lcut(normalized) if word and not word.isspace()]
    if len(words) < 2:
        return None

    shuffled = words[:]
    for _ in range(8):
        random_lib.shuffle(shuffled)
        candidate = "".join(shuffled).strip()
        if candidate and candidate != normalized:
            return candidate
    return None


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


async def _random_enabled(bot: Bot, event: GroupMessageEvent) -> bool:
    """仅在当前群启用了乱序复读功能时触发。"""
    return await is_feature_enabled_async(bot, _FEATURE_KEY, str(event.group_id))


async def _should_handle_random(event: GroupMessageEvent) -> bool:
    """仅在真正需要触发乱序复读时才进入 matcher。"""
    if not _not_from_bot(event):
        return False
    if not await _random_enabled(event):
        return False
    if getattr(event, "_yiyin_repetition_triggered", False):
        return False

    shuffled_reply = _build_shuffled_reply(event.get_plaintext())
    if shuffled_reply is None:
        return False

    state = _load_state()
    group_state = _group_state(state, str(event.group_id))
    should_trigger = _should_trigger_shuffle(group_state)
    _save_state(state)
    if not should_trigger:
        return False

    setattr(event, "_yiyin_random_reply", shuffled_reply)
    return True


random_matcher = on_message(
    Rule(_should_handle_random),
    priority=61,
    block=False,
)


@random_matcher.handle()
async def handle_random_repetition(bot: Bot, event: GroupMessageEvent):
    """处理乱序复读。"""
    shuffled_reply = getattr(event, "_yiyin_random_reply", None)
    if shuffled_reply is None:
        return
    await bot.send(event, shuffled_reply)
