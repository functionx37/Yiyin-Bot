"""随机选项与 A 不 A 问答，引用原消息回复。"""

import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import NetworkError
from nonebot.log import logger
from nonebot.typing import T_State

ChooseEvent = C2CMessageCreateEvent | GroupMessageCreateEvent
COMMAND = re.compile(r"^/选(?:\s+|$)")
YES_NO = re.compile(r"(?P<action>.+)不(?P=action)", re.DOTALL)
_recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()


@dataclass(frozen=True)
class Choice:
    options: tuple[str, ...]
    yes_no: bool = False

    def draw(self) -> str:
        if self.yes_no:
            ticket = random.randrange(100)
            if ticket >= 90:
                return "这也要问？"
            return self.options[0 if ticket < 45 else 1]
        return random.choice(self.options)


def parse_choice(text: str) -> Choice | None:
    text = text.strip()
    if command := COMMAND.match(text):
        options = tuple(filter(None, re.split(r"\s+|还是", text[command.end():])))
    elif text.startswith("#"):
        question = text[1:].strip()
        if "还是" in question:
            options = tuple(option.strip() for option in question.split("还是") if option.strip())
        elif match := YES_NO.search(question):
            prefix, suffix = question[:match.start()], question[match.end():]
            action = match["action"]
            return Choice((f"{prefix}{action}{suffix}", f"{prefix}不{action}{suffix}"), yes_no=True)
        else:
            return None
    else:
        return None
    # Preserve duplicates: each occurrence is an independent entry in the draw.
    return Choice(options) if len(options) >= 2 else None


async def choice_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return False
    if request := parse_choice(event.get_plaintext()):
        state["choose_request"] = request
        return True
    return False


choose = on_message(rule=choice_rule, priority=10, block=True)


async def send_reply(bot: Bot, event: ChooseEvent, answer: str):
    reference = None
    if event.message_scene:
        reference = next((ext.partition("=")[2] for ext in event.message_scene.ext
                          if ext.startswith("msg_idx=")), None)
    message = MessageSegment.reference(event.id) + MessageSegment.text(answer)
    event._reply_seq += 1
    payload = {"message": message, "msg_id": event.id, "msg_seq": event._reply_seq,
               "msg_ref_id": reference}
    if isinstance(event, GroupMessageCreateEvent):
        return await bot.send_to_group(group_openid=event.group_openid, **payload)
    return await bot.send_to_c2c(openid=event.author.user_openid, **payload)


@choose.handle()
async def handle_choose(bot: Bot, event: ChooseEvent, state: T_State):
    now = monotonic()
    while _recent and (next(iter(_recent.values())) <= now - 300 or len(_recent) >= 4096):
        _recent.popitem(last=False)
    key = (bot.self_id, event.get_session_id(), event.id)
    if key in _recent:
        return
    _recent[key] = now
    try:
        await send_reply(bot, event, state["choose_request"].draw())
    except NetworkError:
        logger.warning("QQ 选择回复连接中断，送达结果未知，未自动重复发送")
    except BaseException:
        _recent.pop(key, None)
        raise
