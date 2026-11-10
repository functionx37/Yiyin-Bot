"""QQ 官方塔罗：抽牌、Markdown 卡片与再次抽牌按钮。"""

import asyncio
import json
import random
from base64 import b64encode
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic

from nonebot import on_command
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed, ApiNotAvailable, NetworkError
from nonebot.drivers import Request
from nonebot.log import logger
from PIL import Image

from . import media

ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "assets/documents/tarot.json").open(encoding="utf-8") as source:
    DECK = {card["id"]: card for card in json.load(source)}
if set(DECK) != set(range(22)):
    raise ValueError("塔罗牌数据必须包含编号 0–21")

NUMERALS = ("0", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
            "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI")
TarotEvent = C2CMessageCreateEvent | GroupMessageCreateEvent
tarot = on_command("抽塔罗牌", priority=10, block=True)
_recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_no_keyboard: set[str] = set()
CARD_WIDTH = 180


@dataclass(frozen=True)
class Draw:
    card_id: int
    title: str
    meaning: str
    image: bytes
    size: tuple[int, int]


class DeliveryUncertain(Exception):
    """发送请求断开时无法判断 QQ 是否已收到，不能用新序号自动补发。"""


def error_code(error: Exception):
    body = getattr(error, "body", None) or {}
    return body.get("err_code", getattr(error, "code", None))


def draw_card(card_id: int | None = None, upright: bool | None = None) -> Draw:
    card_id = random.randrange(22) if card_id is None else card_id
    upright = random.choice((True, False)) if upright is None else upright
    card = DECK[card_id]
    image_bytes = (ROOT / f"assets/images/tarot/{card_id}.png").read_bytes()
    with Image.open(BytesIO(image_bytes)) as image:
        size = (CARD_WIDTH, round(CARD_WIDTH * image.height / image.width))
        with image.resize(size, Image.Resampling.LANCZOS) as resized:
            if not upright:
                with resized.transpose(Image.Transpose.ROTATE_180) as rotated, BytesIO() as output:
                    rotated.save(output, format="PNG")
                    image_bytes = output.getvalue()
            else:
                with BytesIO() as output:
                    resized.save(output, format="PNG")
                    image_bytes = output.getvalue()
    return Draw(
        card_id,
        f"【{NUMERALS[card_id]}】{card['name_zh']}（{card['name_en']}）· {'正位' if upright else '逆位'}",
        f"{'正位' if upright else '逆位'}：{card['upright' if upright else 'reversed']}",
        image_bytes,
        size,
    )


def card_markdown(event: TarotEvent, card: Draw, image_url: str) -> str:
    caller = (f'<qqbot-at-user id="{event.author.member_openid}" /> '
              if isinstance(event, GroupMessageCreateEvent) else "你")
    width, height = card.size
    meaning = "\n".join(f"> {line}" for line in card.meaning.splitlines())
    return (f"{caller}抽到了：\n\n**{card.title}**\n\n"
            f"![塔罗牌 #{width}px #{height}px]({image_url})\n\n{meaning}")


def draw_button() -> dict:
    return {"content": {"rows": [{"buttons": [{
        "id": "tarot-draw-again",
        "render_data": {"label": "再抽一次", "visited_label": "再抽一次", "style": 1},
        "action": {
            "type": 2, "permission": {"type": 2}, "data": "/抽塔罗牌",
            "enter": False, "reply": False, "unsupport_tips": "请手动发送 /抽塔罗牌",
        },
    }]}]}}


async def send(bot: Bot, event: TarotEvent, **payload):
    if isinstance(event, GroupMessageCreateEvent):
        scene, openid = "groups", event.group_openid
    else:
        scene, openid = "users", event.author.user_openid
    event._reply_seq += 1
    try:
        return await bot._request(Request(
            "POST", bot.adapter.get_api_base().joinpath("v2", scene, openid, "messages"),
            json={"msg_id": event.id, "msg_seq": event._reply_seq, **payload},
        ))
    except NetworkError as error:
        raise DeliveryUncertain from error


async def send_markdown(bot: Bot, event: TarotEvent, card: Draw):
    for attempt in range(2):
        url = await media.image_url(bot, card.image, refresh=bool(attempt))
        payload = {"msg_type": 2, "markdown": {
            "content": card_markdown(event, card, url), "force_verify_image_resource": True,
        }}
        if bot.self_id not in _no_keyboard:
            payload["keyboard"] = draw_button()
        try:
            try:
                await send(bot, event, **payload)
            except ActionFailed as error:
                if error_code(error) != 304057 or "keyboard" not in payload:
                    raise
                _no_keyboard.add(bot.self_id)
                logger.warning("QQ 未开放自定义按钮权限，继续发送 Markdown 卡片")
                payload.pop("keyboard")
                await send(bot, event, **payload)
        except ActionFailed as error:
            if error_code(error) in {40035008, 40035009, 40035010} and attempt == 0:
                continue
            raise
        await media.mark_used(bot, card.image)
        return


async def send_plain_card(bot: Bot, event: TarotEvent, card: Draw):
    """平台暂不接受 Markdown/素材时仍能完成同一次抽牌。"""
    if isinstance(event, GroupMessageCreateEvent):
        scene, openid = "groups", event.group_openid
    else:
        scene, openid = "users", event.author.user_openid
    uploaded = await bot._request(Request(
        "POST", bot.adapter.get_api_base().joinpath("v2", scene, openid, "files"),
        json={"file_type": 1, "file_name": f"tarot-{card.card_id}.png",
              "file_data": b64encode(card.image).decode("ascii"), "srv_send_msg": False},
        timeout=20,
    ))
    await send(bot, event, msg_type=7, content=f"抽到了：\n{card.title}\n{card.meaning}",
               media={"file_info": uploaded["file_info"]})


@tarot.handle()
async def handle_tarot(bot: Bot, event: TarotEvent):
    now = monotonic()
    while _recent and (next(iter(_recent.values())) <= now - 300 or len(_recent) >= 4096):
        _recent.popitem(last=False)
    key = (bot.self_id, event.get_session_id(), event.id)
    if key in _recent:
        return
    _recent[key] = now
    try:
        card = await asyncio.to_thread(draw_card)
        try:
            await send_markdown(bot, event, card)
        except (ActionFailed, ApiNotAvailable, NetworkError, ValueError) as error:
            logger.warning("QQ Markdown 回复失败，改用图文：{} code={}",
                           type(error).__name__, error_code(error))
            try:
                await send_plain_card(bot, event, card)
            except (ActionFailed, ApiNotAvailable, NetworkError, ValueError, KeyError):
                await send(bot, event, msg_type=0, content=f"抽到了：\n{card.title}\n{card.meaning}")
    except DeliveryUncertain:
        logger.warning("QQ 消息发送连接中断，送达结果未知，未自动重复发送")
    except BaseException:
        _recent.pop(key, None)
        raise
