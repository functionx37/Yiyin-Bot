"""QQ memes: /强强, /男娘 and /想, using the original measured layouts."""

import asyncio
import re
from collections import OrderedDict
from time import monotonic
from urllib.parse import quote

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed, ApiNotAvailable, NetworkError
from nonebot.adapters.qq.models import Attachment
from nonebot.log import logger
from nonebot.typing import T_State

from ..image import _busy, download, send_image
from ..image.operations import MAX_FILE_BYTES, ImageError, Result
from .avatar import composite_avatar, validate_avatar
from .strong import _draw_bibi, _parse_strong_args
from .think import _draw_template, _load_template_map, _parse_xiang_args, _resolve_template

MemeEvent = C2CMessageCreateEvent | GroupMessageCreateEvent
COMMAND = re.compile(r"^/(强强|男娘|想)(?=\s|$)")
MAX_TEXT_LENGTH = 500
_recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()


async def meme_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return False
    text = event.get_plaintext().strip()
    if match := COMMAND.match(text):
        state["meme_command"] = match[1]
        state["meme_argument"] = text[match.end():].strip()
        return True
    return False


meme = on_message(rule=meme_rule, priority=10, block=True)


async def send_text(bot: Bot, event: MemeEvent, text: str):
    event._reply_seq += 1
    payload = {"message": MessageSegment.text(text), "msg_id": event.id, "msg_seq": event._reply_seq}
    if isinstance(event, GroupMessageCreateEvent):
        return await bot.send_to_group(group_openid=event.group_openid, **payload)
    return await bot.send_to_c2c(openid=event.author.user_openid, **payload)


def mentioned_member(event: MemeEvent, bot: Bot) -> str | None:
    if not isinstance(event, GroupMessageCreateEvent):
        return None
    mentions = event.mentions or []
    for segment in event.get_message():
        if segment.type != "mention_user":
            continue
        identifier = segment.data["user_id"]
        member = next((item for item in mentions if getattr(item, "scope", None) == "single"
                       and identifier in {item.id, item.member_openid}), None)
        if member:
            if not member.bot:
                return member.member_openid
        elif identifier not in {bot.self_id, bot.self_info.id, "all"}:
            return identifier
    return None


async def avatar_bytes(bot: Bot, event: MemeEvent) -> bytes:
    caller = event.get_user_id()
    target = mentioned_member(event, bot)
    candidates = [target, caller] if target and target != caller else [caller]
    for openid in candidates:
        url = f"https://q.qlogo.cn/qqapp/{quote(bot.self_id, safe='')}/{quote(openid, safe='')}/640"
        try:
            data = await download(Attachment(content_type="image/png", url=url))
            await asyncio.to_thread(validate_avatar, data)
            return data
        except ImageError:
            continue
    raise ImageError("发起者头像获取失败，请稍后重试。")


def render(command: str, argument: str, avatar: bytes | None = None) -> Result:
    if len(argument) > MAX_TEXT_LENGTH:
        raise ImageError("文字过长：最多 500 个字符。")
    if command == "强强":
        if not re.search(r"（.*?）", argument, re.DOTALL):
            raise ImageError("用法：/强强 （文本1）（文本2）（文本3），支持括号内换行。")
        data = _draw_bibi(_parse_strong_args(argument))
    elif command == "想":
        alias, text = _parse_xiang_args(argument)
        if not text:
            raise ImageError("用法：/想 文本，或 /想 %模板名 文本；/想 list 查看模板。")
        try:
            _, template = _resolve_template(alias)
        except KeyError as error:
            raise ImageError(f"不存在模板：{alias}，请用 /想 list 查看模板。") from error
        data = _draw_template(text, template)
    else:
        if avatar is None:
            raise ImageError("未获取到头像，请稍后重试。")
        data = composite_avatar(avatar)
    if len(data) > MAX_FILE_BYTES:
        raise ImageError("生成的图片超过 10 MB，请减少文字后重试。")
    return Result(data, "meme.png")


async def render_in_thread(command: str, argument: str, avatar: bytes | None) -> Result:
    task = asyncio.create_task(asyncio.to_thread(render, command, argument, avatar))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise cancelled


@meme.handle()
async def handle_meme(bot: Bot, event: MemeEvent, state: T_State):
    now = monotonic()
    while _recent and (next(iter(_recent.values())) <= now - 300 or len(_recent) >= 4096):
        _recent.popitem(last=False)
    key = (bot.self_id, event.get_session_id(), event.id)
    if key in _recent:
        return
    _recent[key] = now
    try:
        command, argument = state["meme_command"], state["meme_argument"]
        if command == "想" and _parse_xiang_args(argument)[0] == "LIST":
            aliases = _load_template_map()
            await send_text(bot, event, "想系列模板：\n" + "\n".join(f"%{name}" for name in aliases)
                            + "\n用法：/想 %模板名 文本；/想 文本 使用默认模板。")
            return
        if _busy.locked():
            raise ImageError("正在处理其他图片，请稍后重试。")
        async with _busy:
            avatar = await avatar_bytes(bot, event) if command == "男娘" else None
            result = await render_in_thread(command, argument, avatar)
            await send_image(bot, event, result)
    except ImageError as error:
        await send_text(bot, event, str(error))
    except NetworkError:
        logger.warning("QQ 表情包回复连接中断，送达结果未知，未自动重复发送")
    except (FileNotFoundError, OSError, ValueError, ActionFailed, ApiNotAvailable, KeyError) as error:
        logger.warning("QQ 表情包生成或发送失败：{}", type(error).__name__)
        await send_text(bot, event, "表情包生成或发送失败，请检查模板、字体是否齐全后重试。")
    except BaseException:
        _recent.pop(key, None)
        raise
