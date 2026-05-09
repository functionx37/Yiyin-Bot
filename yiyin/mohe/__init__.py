"""
NoneBot2 摩诃插件
- 命令：/随机摩诃    — 逐条发送 3-5 条随机摩诃语录
- 命令：/投稿摩诃    — 引用一条消息投稿摩诃文本或图片
"""

import asyncio
import json
import mimetypes
import random
from pathlib import Path
from urllib.parse import urlparse

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.log import logger

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOHE_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "mohe.json"
MOHE_IMAGE_DIR = PROJECT_ROOT / "assets" / "images" / "mohe"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
CONTENT_TYPE_TO_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# ==================== 加载摩诃数据 ====================
with open(MOHE_JSON_PATH, "r", encoding="utf-8") as f:
    _raw_data: list[str] = json.load(f)

# 文本 + 图片统一池
MOHE_DATA: list[str | Path] = [s for s in _raw_data if s.strip()]

if MOHE_IMAGE_DIR.is_dir():
    for img in sorted(MOHE_IMAGE_DIR.iterdir()):
        if img.suffix.lower() in IMAGE_SUFFIXES:
            MOHE_DATA.append(img)


def _to_message(item: str | Path):
    """将数据项转为可发送的消息"""
    if isinstance(item, Path):
        return MessageSegment.image(item.read_bytes())
    return item


def _load_mohe_texts() -> list[str]:
    """读取 mohe.json 中的文本列表。"""
    with open(MOHE_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [item for item in data if isinstance(item, str) and item.strip()]


def _save_mohe_texts(texts: list[str]) -> None:
    """写回 mohe.json。"""
    with open(MOHE_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(texts, f, ensure_ascii=False, indent=4)
        f.write("\n")


def _next_mohe_image_path(content_type: str | None, url: str | None) -> Path:
    """按 assets/images/mohe 现有数字命名规则生成下一个文件路径。"""
    max_index = 0
    if MOHE_IMAGE_DIR.is_dir():
        for item in MOHE_IMAGE_DIR.iterdir():
            if item.is_file() and item.stem.isdigit():
                max_index = max(max_index, int(item.stem))

    suffix = ".png"
    if content_type:
        normalized = content_type.split(";", maxsplit=1)[0].strip().lower()
        suffix = CONTENT_TYPE_TO_SUFFIX.get(
            normalized, mimetypes.guess_extension(normalized) or suffix
        )
    if url:
        url_suffix = Path(urlparse(url).path).suffix.lower()
        if url_suffix in IMAGE_SUFFIXES:
            suffix = url_suffix

    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".png"

    return MOHE_IMAGE_DIR / f"{max_index + 1}{suffix}"


async def _extract_reply_images(bot: Bot, event: GroupMessageEvent) -> list[MessageSegment]:
    """从引用消息中提取图片。"""
    if not event.reply:
        return []

    reply_images: list[MessageSegment] = []
    if event.reply.message:
        reply_images = [seg for seg in event.reply.message if seg.type == "image"]
    if reply_images:
        return reply_images

    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw_msg = msg_data.get("message", [])
        if isinstance(raw_msg, Message):
            return [seg for seg in raw_msg if seg.type == "image"]
        if isinstance(raw_msg, str):
            parsed = Message(raw_msg)
            return [seg for seg in parsed if seg.type == "image"]
        if isinstance(raw_msg, list):
            for seg in raw_msg:
                if isinstance(seg, MessageSegment) and seg.type == "image":
                    reply_images.append(seg)
                elif isinstance(seg, dict) and seg.get("type") == "image":
                    reply_images.append(MessageSegment("image", seg.get("data", {})))
    except Exception:
        logger.exception("提取摩诃引用图片失败")

    return reply_images


async def _extract_reply_text(bot: Bot, event: GroupMessageEvent) -> str:
    """从引用消息中提取纯文本。"""
    if not event.reply:
        return ""

    if event.reply.message:
        text = event.reply.message.extract_plain_text().strip()
        if text:
            return text

    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw_msg = msg_data.get("message", [])
        if isinstance(raw_msg, Message):
            return raw_msg.extract_plain_text().strip()
        if isinstance(raw_msg, str):
            return Message(raw_msg).extract_plain_text().strip()
        if isinstance(raw_msg, list):
            return "".join(
                seg.get("data", {}).get("text", "")
                for seg in raw_msg
                if isinstance(seg, dict) and seg.get("type") == "text"
            ).strip()
    except Exception:
        logger.exception("提取摩诃引用文字失败")

    return ""


async def _save_reply_images(images: list[MessageSegment]) -> list[Path]:
    """下载并保存引用消息中的图片。"""
    saved_paths: list[Path] = []
    MOHE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for image in images:
            url = image.data.get("url")
            if not url:
                continue

            resp = await client.get(url)
            resp.raise_for_status()

            save_path = _next_mohe_image_path(resp.headers.get("content-type"), url)
            save_path.write_bytes(resp.content)
            saved_paths.append(save_path)

    return saved_paths


# ==================== 注册命令 ====================
random_mohe_cmd = on_command("随机摩诃", priority=10, block=True)
submit_mohe_cmd = on_command("投稿摩诃", priority=10, block=True)


@random_mohe_cmd.handle()
async def handle_random_mohe(bot: Bot, event: GroupMessageEvent):
    """处理 /随机摩诃 命令：逐条发送 3-5 条随机摩诃语录"""
    count = random.randint(3, 5)
    selected = random.sample(MOHE_DATA, min(count, len(MOHE_DATA)))

    for i, item in enumerate(selected):
        await bot.send(event, _to_message(item))
        if i < len(selected) - 1:
            await asyncio.sleep(random.uniform(1, 3))


@submit_mohe_cmd.handle()
async def handle_submit_mohe(bot: Bot, event: GroupMessageEvent):
    """处理 /投稿摩诃 命令：引用图片则下载，引用文字则写入 JSON。"""
    if not event.reply:
        await submit_mohe_cmd.finish("请引用一条消息后再发送：/投稿摩诃")

    images = await _extract_reply_images(bot, event)
    if images:
        try:
            saved_paths = await _save_reply_images(images)
        except Exception:
            logger.exception("保存摩诃图片失败")
            await submit_mohe_cmd.finish("保存图片失败，请稍后重试")

        if not saved_paths:
            await submit_mohe_cmd.finish("引用的消息包含图片，但未获取到可下载的图片链接")

        MOHE_DATA.extend(saved_paths)
        await submit_mohe_cmd.finish(f"已保存 {len(saved_paths)} 张摩诃图片")

    text = await _extract_reply_text(bot, event)
    if not text:
        await submit_mohe_cmd.finish("引用的消息里没有可投稿的文字或图片")

    try:
        texts = _load_mohe_texts()
        texts.append(text)
        _save_mohe_texts(texts)
    except Exception:
        logger.exception("写入摩诃文本失败")
        await submit_mohe_cmd.finish("写入摩诃文案失败，请稍后重试")

    MOHE_DATA.append(text)
    await submit_mohe_cmd.finish("已写入 mohe.json")
