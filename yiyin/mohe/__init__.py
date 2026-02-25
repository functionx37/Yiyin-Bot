"""
NoneBot2 摩诃插件
- 命令：/随机摩诃    — 逐条发送 3-5 条随机摩诃语录
"""

import asyncio
import json
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOHE_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "mohe.json"
MOHE_IMAGE_DIR = PROJECT_ROOT / "assets" / "images" / "mohe"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

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


# ==================== 注册命令 ====================
random_mohe_cmd = on_command("随机摩诃", priority=10, block=True)


@random_mohe_cmd.handle()
async def handle_random_mohe(bot: Bot, event: GroupMessageEvent):
    """处理 /随机摩诃 命令：逐条发送 3-5 条随机摩诃语录"""
    count = random.randint(3, 5)
    selected = random.sample(MOHE_DATA, min(count, len(MOHE_DATA)))

    for i, item in enumerate(selected):
        await bot.send(event, _to_message(item))
        if i < len(selected) - 1:
            await asyncio.sleep(random.uniform(1, 3))
