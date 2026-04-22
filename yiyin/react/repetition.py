"""
复读功能（react 子模块，隐藏功能，需 /启用 复读）
- 若群友连续发送两条完全相同的文字/QQ 系统表情消息，机器人复读一次
- 同一条消息在短时间内只复读一次，避免循环复读
- 另外对每个群维护独立的渐进随机概率，平均约每 100 条可处理消息乱序复读一次
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import jieba
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import Rule

from yiyin.toggle import is_feature_enabled

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "react" / "repetition"
STATE_PATH = DATA_DIR / "state.json"

_FEATURE_KEY = "yiyin.react.repetition"
_REPEAT_COOLDOWN_SECONDS = 600
_RANDOM_BASE_PROBABILITY = 0.0008
_RANDOM_INCREMENT = 0.00014
_RANDOM_MAX_PROBABILITY = 0.05
_RECENT_REPEAT_LIMIT = 100
_STATE_SAVE_INTERVAL_SECONDS = 5

_state_cache: dict | None = None
_state_dirty = False
_last_save_time = 0.0


def _load_state() -> dict:
    """读取运行时状态，文件损坏时回退为空状态。"""
    global _state_cache
    if _state_cache is not None:
        return _state_cache

    if not STATE_PATH.exists():
        _state_cache = {"groups": {}}
        return _state_cache

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {"groups": {}}

    if not isinstance(data, dict):
        data = {"groups": {}}
    data.setdefault("groups", {})
    _state_cache = data
    return _state_cache


def _mark_state_dirty() -> None:
    """标记运行时状态已变更。"""
    global _state_dirty
    _state_dirty = True


def _save_state(*, force: bool = False) -> None:
    """按需保存运行时状态，避免每条消息都落盘。"""
    global _state_dirty, _last_save_time
    if not _state_dirty:
        return

    now = time.time()
    if not force and now - _last_save_time < _STATE_SAVE_INTERVAL_SECONDS:
        return

    state = _load_state()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    _state_dirty = False
    _last_save_time = now


def _group_state(group_id: str) -> dict:
    """获取单个群的状态字典。"""
    groups = _load_state().setdefault("groups", {})
    return groups.setdefault(
        group_id,
        {
            "last_signature": None,
            "last_count": 0,
            "recent_repeats": {},
            "random_failures": 0,
        },
    )


def _message_signature(message: Message) -> str | None:
    """仅为文字和 QQ 系统表情生成可比较签名。"""
    parts: list[list[str]] = []
    for segment in message:
        if segment.type == "text":
            parts.append(["text", segment.data.get("text", "")])
            continue
        if segment.type == "face":
            parts.append(["face", str(segment.data.get("id", ""))])
            continue
        return None

    if not parts:
        return None
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _prune_recent_repeats(group_state: dict, now: float) -> None:
    """清理过期的复读冷却记录。"""
    recent = group_state.setdefault("recent_repeats", {})
    expired = [
        signature
        for signature, ts in recent.items()
        if now - float(ts) >= _REPEAT_COOLDOWN_SECONDS
    ]
    for signature in expired:
        recent.pop(signature, None)

    if len(recent) > _RECENT_REPEAT_LIMIT:
        for signature, _ in sorted(recent.items(), key=lambda item: item[1])[
            : len(recent) - _RECENT_REPEAT_LIMIT
        ]:
            recent.pop(signature, None)


def _update_repeat_counter(group_state: dict, signature: str | None) -> None:
    """更新连续相同消息计数。"""
    if signature is None:
        group_state["last_signature"] = None
        group_state["last_count"] = 0
        return

    if group_state.get("last_signature") == signature:
        group_state["last_count"] = int(group_state.get("last_count", 0)) + 1
        return

    group_state["last_signature"] = signature
    group_state["last_count"] = 1


def _should_repeat(group_state: dict, signature: str | None, now: float) -> bool:
    """判断当前消息是否应该触发复读。"""
    if signature is None:
        return False

    _prune_recent_repeats(group_state, now)
    if int(group_state.get("last_count", 0)) != 2:
        return False
    if signature in group_state.get("recent_repeats", {}):
        return False

    group_state.setdefault("recent_repeats", {})[signature] = now
    return True


def _should_trigger_shuffle(group_state: dict) -> bool:
    """按渐进概率判断是否触发乱序复读。"""
    failures = int(group_state.get("random_failures", 0))
    probability = min(
        _RANDOM_BASE_PROBABILITY + failures * _RANDOM_INCREMENT,
        _RANDOM_MAX_PROBABILITY,
    )
    if random.random() < probability:
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
        random.shuffle(shuffled)
        candidate = "".join(shuffled).strip()
        if candidate and candidate != normalized:
            return candidate
    return None


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """忽略机器人自己的消息。"""
    return str(event.self_id) != str(event.user_id)


async def _repetition_enabled(event: GroupMessageEvent) -> bool:
    """仅在当前群启用了复读功能时触发。"""
    return is_feature_enabled(_FEATURE_KEY, str(event.group_id))


repetition_matcher = on_message(
    Rule(_not_from_bot, _repetition_enabled),
    priority=61,
    block=False,
)


@repetition_matcher.handle()
async def handle_repetition(bot: Bot, event: GroupMessageEvent):
    """处理群消息复读与乱序复读。"""
    group_id = str(event.group_id)
    group_state = _group_state(group_id)
    now = time.time()

    signature = _message_signature(event.message)
    _update_repeat_counter(group_state, signature)
    _mark_state_dirty()

    if _should_repeat(group_state, signature, now):
        _save_state()
        await bot.send(event, Message(event.message))
        return

    shuffled_reply = _build_shuffled_reply(event.get_plaintext())
    if shuffled_reply is None:
        _save_state()
        return

    if not _should_trigger_shuffle(group_state):
        _save_state()
        return

    _save_state()
    await bot.send(event, shuffled_reply)
