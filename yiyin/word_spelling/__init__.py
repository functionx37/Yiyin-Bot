"""
NoneBot2 word拼词插件
- 当前指令：/dqxm [n]、/ccb [n]
- 新增通用指令：/拼 <声母序列> [n]
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

from yiyin.word_spelling.explain import explain_dqxm_word

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PINYIN_DIR = PROJECT_ROOT / "assets" / "documents" / "pinyin"

MAX_COUNT = 100
DIRECT_SEND_THRESHOLD = 10
CHUNK_SIZE = 10


class WordRule(TypedDict):
    pinyin_slots: list[str | list[str]]


# 可扩展规则：新增指令时只需补充配置项并注册 matcher
WORD_RULES: dict[str, WordRule] = {
    "dqxm": {
        "pinyin_slots": ["d", "q", "x", "m"],
    },
    "ccb": {
        "pinyin_slots": [["c", "ch"], ["c", "ch"], "b"],
    }
}

_PINYIN_CACHE: dict[str, list[str]] = {}
_AVAILABLE_PINYIN_KEYS = sorted(
    (path.stem for path in PINYIN_DIR.glob("*.json")),
    key=lambda item: (-len(item), item),
)


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


def _parse_pinyin_pattern(pattern: str) -> tuple[list[str | list[str]] | None, str | None]:
    normalized = pattern.strip().lower()
    if not normalized:
        return None, "用法：/拼 <声母序列> [n]"
    if normalized[0] in {"/", "'"}:
        return None, f"声母序列不能以 {normalized[0]} 开头：{pattern}"

    tokens: list[str] = []
    linked_with_previous: list[bool] = []
    index = 0
    next_token_linked = False
    while index < len(normalized):
        matched_key = next(
            (key for key in _AVAILABLE_PINYIN_KEYS if normalized.startswith(key, index)),
            None,
        )
        if matched_key is None:
            return None, f"无法识别的声母序列：{pattern}"

        tokens.append(matched_key)
        linked_with_previous.append(next_token_linked)
        next_token_linked = False
        index += len(matched_key)
        if index >= len(normalized):
            break

        separator = normalized[index]
        if separator == "'":
            index += 1
            if index >= len(normalized):
                return None, f"声母序列不能以 ' 结尾：{pattern}"
            continue

        if separator == "/":
            next_token_linked = True
            index += 1
            if index >= len(normalized):
                return None, f"声母序列不能以 / 结尾：{pattern}"

    slots: list[str | list[str]] = []
    current_slot: list[str] = [tokens[0]]
    for idx in range(1, len(tokens)):
        if linked_with_previous[idx]:
            current_slot.append(tokens[idx])
            continue

        slots.append(current_slot[0] if len(current_slot) == 1 else current_slot.copy())
        current_slot = [tokens[idx]]

    slots.append(current_slot[0] if len(current_slot) == 1 else current_slot.copy())
    return slots, None


def _generate_word(pinyin_slots: list[str | list[str]]) -> str:
    chars: list[str] = []
    for slot in pinyin_slots:
        if isinstance(slot, str):
            pinyin_key = slot
        else:
            pinyin_key = random.choice(slot)
        chars.append(random.choice(_load_pinyin_chars(pinyin_key)))
    return "".join(chars)


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


def _parse_dynamic_command_args(args_text: str) -> tuple[list[str | list[str]] | None, int | None, str | None]:
    parts = args_text.split()
    if not parts:
        return None, None, "用法：/拼 <声母序列> [n]"
    if len(parts) > 2:
        return None, None, "用法：/拼 <声母序列> [n]"

    pattern = parts[0]
    count_text = parts[1] if len(parts) == 2 else ""

    pinyin_slots, error = _parse_pinyin_pattern(pattern)
    if error:
        return None, None, error

    count, error = _parse_count(count_text, "拼")
    if error:
        return None, None, error

    return pinyin_slots, count, None


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


def _extract_message_id(response) -> int | None:
    """从 OneBot 发送结果中提取 message_id。"""
    if not isinstance(response, dict):
        return None
    message_id = response.get("message_id")
    if message_id is not None:
        return int(message_id)
    data = response.get("data")
    if isinstance(data, dict) and data.get("message_id") is not None:
        return int(data["message_id"])
    return None


async def _send_words(
    bot: Bot,
    event: GroupMessageEvent,
    words: list[str],
) -> int | None:
    if len(words) <= DIRECT_SEND_THRESHOLD:
        response = await bot.send(event, "\n".join(words))
        return _extract_message_id(response)

    bot_info = await bot.get_login_info()
    bot_name = bot_info.get("nickname", "YiyinBot")
    bot_uin = str(bot.self_id)
    nodes = _build_forward_nodes(bot_name, bot_uin, words)
    response = await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    return _extract_message_id(response)


async def _send_dqxm_explanation(
    bot: Bot,
    event: GroupMessageEvent,
    words: list[str],
    sent_message_id: int | None,
) -> None:
    """为 /dqxm 生成的单个词语补充一本正经胡说八道式解释。"""
    if len(words) != 1 or sent_message_id is None:
        return

    explanation = await explain_dqxm_word(words[0])
    if not explanation:
        return

    reply_msg = MessageSegment.reply(sent_message_id) + MessageSegment.text(explanation)
    await bot.send(event, reply_msg)


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
        sent_message_id = await _send_words(bot, event, words)
        if command == "dqxm":
            await _send_dqxm_explanation(bot, event, words, sent_message_id)
        await matcher.finish()


spell_matcher = on_command("拼", priority=10, block=True)


@spell_matcher.handle()
async def _handle_spell_command(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
):
    pinyin_slots, count, error = _parse_dynamic_command_args(
        args.extract_plain_text().strip()
    )
    if error:
        await spell_matcher.finish(error)
    assert pinyin_slots is not None
    assert count is not None

    words = [_generate_word(pinyin_slots) for _ in range(count)]
    await _send_words(bot, event, words)
    await spell_matcher.finish()


for _command in WORD_RULES:
    _register_word_command(_command)

