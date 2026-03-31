"""
NoneBot2 word拼词插件
- 当前指令：/dqxm [n]、/ccb [n]
- 规则：按命令规则从拼音字库随机取字拼词
- 扩展：通过 WORD_RULES 配置可继续新增同类指令
"""

import json
import random
from pathlib import Path
from typing import TypedDict

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PINYIN_DIR = PROJECT_ROOT / "assets" / "documents" / "pinyin"
DATA_DIR = PROJECT_ROOT / "data" / "word_spelling"

MAX_COUNT = 100
DIRECT_SEND_THRESHOLD = 10
CHUNK_SIZE = 10


class WordRule(TypedDict):
    pinyin_slots: list[str | list[str]]
    data_file: Path


# 可扩展规则：新增指令时只需补充配置项并注册 matcher
WORD_RULES: dict[str, WordRule] = {
    "dqxm": {
        "pinyin_slots": ["d", "q", "x", "m"],
        "data_file": DATA_DIR / "dqxm.json",
    },
    "ccb": {
        "pinyin_slots": [["c", "ch"], ["c", "ch"], "b"],
        "data_file": DATA_DIR / "ccb.json",
    }
}

_PINYIN_CACHE: dict[str, list[str]] = {}


def _load_pinyin_chars(pinyin_key: str) -> list[str]:
    if pinyin_key in _PINYIN_CACHE:
        return _PINYIN_CACHE[pinyin_key]

    path = PINYIN_DIR / f"{pinyin_key}.json"
    with open(path, "r", encoding="utf-8") as f:
        chars = json.load(f)

    if not isinstance(chars, list) or not chars:
        raise ValueError(f"拼音字库无效或为空: {path}")

    _PINYIN_CACHE[pinyin_key] = chars
    return chars


def _generate_word(pinyin_slots: list[str | list[str]]) -> str:
    chars: list[str] = []
    for slot in pinyin_slots:
        if isinstance(slot, str):
            pinyin_key = slot
        else:
            pinyin_key = random.choice(slot)
        chars.append(random.choice(_load_pinyin_chars(pinyin_key)))
    return "".join(chars)


def _load_recorded_words(path: Path) -> list[str]:
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return []

    words: list[str] = []
    for item in data:
        if isinstance(item, str) and item:
            words.append(item)
    return words


def _append_unique_words(path: Path, words: list[str]) -> None:
    existing_words = _load_recorded_words(path)
    seen = set(existing_words)
    new_words = [word for word in words if word not in seen]
    if not new_words:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    merged = existing_words + new_words
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def _parse_count(args_text: str, command: str) -> tuple[int | None, str | None]:
    if not args_text:
        return 1, None

    try:
        count = int(args_text)
    except ValueError:
        return None, f"参数 n 必须是整数，用法：/{command} [n]"

    if count <= 0:
        return None, f"参数 n 必须大于 0，用法：/{command} [n]"

    if count > MAX_COUNT:
        count = MAX_COUNT
    return count, None


def _build_forward_nodes(bot_name: str, bot_uin: str, words: list[str]) -> list[dict]:
    nodes: list[dict] = []
    for i in range(0, len(words), CHUNK_SIZE):
        chunk = words[i : i + CHUNK_SIZE]
        text = "\n".join(chunk)
        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot_uin,
                    "content": Message(MessageSegment.text(text)),
                },
            }
        )
    return nodes


def _register_word_command(command: str):
    matcher = on_command(command, priority=10, block=True)
    rule = WORD_RULES[command]

    @matcher.handle()
    async def _handle(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
        count, error = _parse_count(args.extract_plain_text().strip(), command)
        if error:
            await matcher.finish(error)
        assert count is not None

        words = [_generate_word(rule["pinyin_slots"]) for _ in range(count)]
        _append_unique_words(rule["data_file"], words)

        if count <= DIRECT_SEND_THRESHOLD:
            await matcher.finish("\n".join(words))

        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "YiyinBot")
        bot_uin = str(bot.self_id)
        nodes = _build_forward_nodes(bot_name, bot_uin, words)
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)


for _command in WORD_RULES:
    _register_word_command(_command)

