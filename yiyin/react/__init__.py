"""为没有匹配到选择指令的 # 消息发送随机引用回复。"""

import json
import random
import re
from collections import OrderedDict
from pathlib import Path
from time import monotonic

from nonebot import on_message, require
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import NetworkError
from nonebot.log import logger

choose_plugin = require("yiyin.choose")

ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "assets/documents/react.json").open(encoding="utf-8") as source:
    REPLIES = json.load(source)
if not isinstance(REPLIES, list) or not REPLIES or not all(
    isinstance(reply, str) and reply.strip() for reply in REPLIES
):
    raise ValueError("react.json 必须包含至少一条非空文本回复")

ReactEvent = C2CMessageCreateEvent | GroupMessageCreateEvent
CHAIN_HEADER = re.compile(r"^接龙(?=$|[\s:：])")
FIRST_ITEM = re.compile(r"(?:^|\s)[1１][.．、](?!\d)")
_recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()


async def react_rule(event: Event) -> bool:
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return False
    sender_is_bot = getattr(event.author, "bot", None)
    if sender_is_bot:
        return False

    text = event.get_plaintext().strip()
    if not text.startswith("#") or text.count("#") > 1 or "＃" in text:
        return False
    if isinstance(event, GroupMessageCreateEvent) and sender_is_bot is None:
        if text.startswith(("# ", "#\t")):
            return False

    body = text[1:].strip()
    if not body or CHAIN_HEADER.match(body):
        return False
    if ("接龙" in body or "截止时间" in body) and FIRST_ITEM.search(body):
        return False
    return choose_plugin.parse_choice(text) is None


react = on_message(rule=react_rule, priority=20, block=True)


@react.handle()
async def handle_react(bot: Bot, event: ReactEvent):
    now = monotonic()
    while _recent and (next(iter(_recent.values())) <= now - 300 or len(_recent) >= 4096):
        _recent.popitem(last=False)
    key = (bot.self_id, event.get_session_id(), event.id)
    if key in _recent:
        return
    _recent[key] = now
    try:
        await choose_plugin.send_reply(bot, event, random.choice(REPLIES))
    except NetworkError:
        logger.warning("QQ 随机回复连接中断，送达结果未知，未自动重复发送")
    except BaseException:
        _recent.pop(key, None)
        raise
