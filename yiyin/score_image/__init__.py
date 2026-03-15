"""
NoneBot2 成绩图处理插件
- /<类别> [图片]：使用大模型为游戏成绩图添加对应风格的特效
- /黑白 [图片]：本地生成黑白成绩图（不调用大模型）
- /随机成绩图 [图片]：随机选择一种风格处理
- /成绩图列表：以合并转发消息展示所有可用指令及说明
"""

import json
import random
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from nonebot import on_command
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg

from yiyin.llmapi import generate_image_edit

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCORE_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "score.json"

# ==================== 加载配置 ====================
with open(SCORE_JSON_PATH, "r", encoding="utf-8") as _f:
    SCORE_CONFIG: dict[str, str] = json.load(_f)

SPECIAL_COMMANDS = {"随机成绩图", "黑白"}
LLM_COMMANDS = {k: v for k, v in SCORE_CONFIG.items() if k not in SPECIAL_COMMANDS}

IMAGE_MODEL = "gpt-image-1.5"


def _extract_image_url(msg: Message) -> str | None:
    for seg in msg:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                return url
    return None


async def _download_image(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content




def _to_grayscale(image_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(image_bytes))
    gray = img.convert("L")
    buf = BytesIO()
    gray.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


async def _get_image_url(event: GroupMessageEvent, args: Message) -> str | None:
    """从命令参数或引用消息中提取图片 URL"""
    url = _extract_image_url(args)
    if url:
        return url
    if event.reply:
        url = _extract_image_url(event.reply.message)
        if url:
            return url
    return None


async def _handle_style(
    bot: Bot,
    event: GroupMessageEvent,
    matcher,
    style_name: str,
    prompt: str,
    image_url: str,
):
    """调用大模型处理成绩图"""
    await matcher.send(f"🎨 正在生成「{style_name}」风格成绩图，请稍候…")

    try:
        image_bytes = await _download_image(image_url)
    except Exception as e:
        logger.exception(f"下载成绩图失败: {e}")
        await matcher.finish(f"图片下载失败：{e}")

    try:
        results = await generate_image_edit(
            prompt,
            image_bytes,
            model=IMAGE_MODEL,
            size="auto",
            quality="high",
            timeout=180,
        )
    except Exception as e:
        logger.exception(f"成绩图生成异常: {e}")
        await matcher.finish(f"生成失败：{e}")

    if not results:
        await matcher.finish("生成失败：未能获取到生成结果，请稍后重试")

    await matcher.finish(MessageSegment.image(results[0]))


# ==================== /成绩图列表 ====================
score_list_cmd = on_command("成绩图列表", priority=10, block=True)


@score_list_cmd.handle()
async def handle_score_list(bot: Bot, event: GroupMessageEvent):
    bot_info = await bot.get_login_info()
    bot_name = bot_info.get("nickname", "YiyinBot")
    bot_uin = str(bot.self_id)

    nodes = []
    for cmd_name, description in SCORE_CONFIG.items():
        text = f"/{cmd_name}\n{description}"
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

    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)


# ==================== /黑白 ====================
bw_cmd = on_command("黑白", priority=10, block=True)


@bw_cmd.handle()
async def handle_bw(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    image_url = await _get_image_url(event, args)
    if not image_url:
        await bw_cmd.finish("请附带图片或回复一张成绩图，例如：\n/黑白 [图片]")

    await bw_cmd.send("🎨 正在生成黑白成绩图…")

    try:
        image_bytes = await _download_image(image_url)
        result = _to_grayscale(image_bytes)
    except Exception as e:
        logger.exception(f"黑白图片处理失败: {e}")
        await bw_cmd.finish(f"处理失败：{e}")

    await bw_cmd.finish(MessageSegment.image(result))


# ==================== /随机成绩图 ====================
random_score_cmd = on_command("随机成绩图", priority=10, block=True)


@random_score_cmd.handle()
async def handle_random_score(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    image_url = await _get_image_url(event, args)
    if not image_url:
        await random_score_cmd.finish(
            "请附带图片或回复一张成绩图，例如：\n/随机成绩图 [图片]"
        )

    style_name = random.choice(list(LLM_COMMANDS.keys()))
    prompt = LLM_COMMANDS[style_name]

    await _handle_style(bot, event, random_score_cmd, style_name, prompt, image_url)


# ==================== 动态注册各风格指令 ====================
_style_matchers = {}

for _style_name in LLM_COMMANDS:
    _matcher = on_command(_style_name, priority=10, block=True)
    _style_matchers[_style_name] = _matcher


def _make_handler(style_name: str, prompt: str, matcher):
    async def handler(
        bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
    ):
        image_url = await _get_image_url(event, args)
        if not image_url:
            await matcher.finish(
                f"请附带图片或回复一张成绩图，例如：\n/{style_name} [图片]"
            )

        await _handle_style(bot, event, matcher, style_name, prompt, image_url)

    return handler


for _style_name, _prompt in LLM_COMMANDS.items():
    _m = _style_matchers[_style_name]
    _m.handle()(_make_handler(_style_name, _prompt, _m))
