"""QQ image commands, inline/referenced attachments and bounded downloads."""

import asyncio
import re
from base64 import b64encode
from collections import OrderedDict
from contextlib import aclosing
from io import BytesIO
from time import monotonic
from urllib.parse import urlsplit

from nonebot import get_driver, on_message
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed, ApiNotAvailable, NetworkError
from nonebot.adapters.qq.models import Attachment
from nonebot.drivers import Request
from nonebot.log import logger
from nonebot.typing import T_State

from .operations import MAX_FILE_BYTES, ImageError, Operation, Result, check_size, process

ImageEvent = C2CMessageCreateEvent | GroupMessageCreateEvent
COMMAND = re.compile(r"^/(对称|倒放|加速|减速|翻转|旋转|转)(?=\s|$)")
ARGUMENTS = {"对称": ("左", "右", "上", "下"), "翻转": ("左右", "上下"),
             "转": ("90", "180", "270"), "旋转": ("顺", "逆")}
_busy = asyncio.Lock()
_recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()


async def image_rule(event: Event, state: T_State) -> bool:
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return False
    text = event.get_plaintext().strip()
    if match := COMMAND.match(text):
        name = match[1]
        argument = text[match.end():].strip()
        allowed = ARGUMENTS.get(name, ())
        if (allowed and argument and argument not in allowed) or (not allowed and argument):
            usage = f"/{name} [{' / '.join(allowed)}] [图片]" if allowed else f"/{name} [动图]"
            state["image_error"] = f"参数不正确，用法：{usage}。也可以引用含图片的消息。"
        state["image_operation"] = Operation(name, argument or (allowed[0] if allowed else ""))
        return True
    return False


image = on_message(rule=image_rule, priority=10, block=True)


def first_image(event: ImageEvent) -> Attachment | None:
    def find(attachments):
        return next((item for item in attachments or [] if item.content_type.startswith("image/")), None)

    # An inline image always wins, even if its URL/metadata is invalid.
    if attachment := find(event.attachments):
        return attachment
    if event.reply:
        return find(event.reply.attachments)
    if event.message_scene:
        reference = next((ext.partition("=")[2] for ext in event.message_scene.ext
                          if ext.startswith("ref_msg_idx=")), None)
        if reference:
            reply = next((item for item in event.msg_elements or [] if item.msg_idx == reference), None)
            if reply:
                return find(reply.attachments)
    return None


async def download(attachment: Attachment) -> bytes:
    if (attachment.size or 0) > MAX_FILE_BYTES:
        raise ImageError("图片文件过大：最多 10 MB。")
    if attachment.width and attachment.height:
        check_size((attachment.width, attachment.height))
    url = attachment.url or ""
    if urlsplit(url).scheme not in {"https", "http"} or not urlsplit(url).hostname:
        raise ImageError("图片下载地址无效，请重新发送图片。")

    async def read():
        responses = get_driver().stream_request(Request("GET", url, timeout=10), chunk_size=65536)
        with BytesIO() as output:
            async with aclosing(responses):
                async for response in responses:
                    if response.status_code != 200:
                        raise ImageError("图片下载失败，图片链接可能已过期，请重新发送。")
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > MAX_FILE_BYTES:
                        raise ImageError("图片文件过大：最多 10 MB。")
                    chunk = response.content
                    if not isinstance(chunk, bytes):
                        raise ImageError("图片下载失败，请重新发送图片。")
                    if output.tell() + len(chunk) > MAX_FILE_BYTES:
                        raise ImageError("图片文件过大：最多 10 MB。")
                    output.write(chunk)
            if not output.tell():
                raise ImageError("图片下载为空，请重新发送图片。")
            return output.getvalue()

    try:
        return await asyncio.wait_for(read(), timeout=30)
    except ImageError:
        raise
    except Exception as error:
        raise ImageError("图片下载失败或超时，请重新发送图片。") from error


def reference_message(event: ImageEvent):
    reference = next((ext.partition("=")[2] for ext in event.message_scene.ext
                      if ext.startswith("msg_idx=")), None) if event.message_scene else None
    return MessageSegment.reference(reference or event.id)


async def send(bot: Bot, event: ImageEvent, message):
    event._reply_seq += 1
    payload = {"message": reference_message(event) + message, "msg_id": event.id,
               "msg_seq": event._reply_seq}
    if isinstance(event, GroupMessageCreateEvent):
        return await bot.send_to_group(group_openid=event.group_openid, **payload)
    return await bot.send_to_c2c(openid=event.author.user_openid, **payload)


async def send_image(bot: Bot, event: ImageEvent, result: Result):
    # Upload without sending; only the final message consumes a reply sequence.
    scene, openid = ("groups", event.group_openid) if isinstance(event, GroupMessageCreateEvent) \
        else ("users", event.author.user_openid)
    uploaded = await bot._request(Request(
        "POST", bot.adapter.get_api_base().joinpath("v2", scene, openid, "files"),
        json={"file_type": 1, "file_name": result.filename,
              "file_data": b64encode(result.data).decode("ascii"), "srv_send_msg": False}, timeout=30,
    ))
    event._reply_seq += 1
    await bot._request(Request(
        "POST", bot.adapter.get_api_base().joinpath("v2", scene, openid, "messages"),
        json={"msg_type": 7, "media": {"file_info": uploaded["file_info"]},
              "msg_id": event.id, "msg_seq": event._reply_seq},
    ))


async def process_in_thread(data: bytes, operation: Operation) -> Result:
    task = asyncio.create_task(asyncio.to_thread(process, data, operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # Cancelling to_thread does not stop Pillow. Keep the processing slot
        # occupied until its memory has been released before accepting more work.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a possible worker exception without replacing cancellation.
        if not task.cancelled():
            task.exception()
        raise cancelled


@image.handle()
async def handle_image(bot: Bot, event: ImageEvent, state: T_State):
    now = monotonic()
    while _recent and (next(iter(_recent.values())) <= now - 300 or len(_recent) >= 4096):
        _recent.popitem(last=False)
    key = (bot.self_id, event.get_session_id(), event.id)
    if key in _recent:
        return
    _recent[key] = now
    try:
        if error := state.get("image_error"):
            raise ImageError(error)
        attachment = first_image(event)
        if attachment is None:
            raise ImageError("没有找到图片，请在指令中附带图片，或引用一条含图片的消息。")
        if _busy.locked():
            raise ImageError("正在处理其他图片，请稍后重试。")
        async with _busy:
            data = await download(attachment)
            result = await process_in_thread(data, state["image_operation"])
            try:
                await send_image(bot, event, result)
            except NetworkError:
                # Sending may already have succeeded; never send a second reply.
                logger.warning("QQ 图片发送连接中断，送达结果未知，未自动重复发送")
            except (ActionFailed, ApiNotAvailable, KeyError) as error:
                logger.warning("QQ 图片发送失败：{}", type(error).__name__)
                raise ImageError("图片发送失败，请稍后重试或使用较小的图片。") from error
    except ImageError as error:
        await send(bot, event, MessageSegment.text(str(error)))
    except BaseException:
        _recent.pop(key, None)
        raise
